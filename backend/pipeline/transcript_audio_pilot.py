"""Source-bound audio pilot. Raw ASR is immutable; no sermon sources are written."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import time
import wave

import numpy as np

from backend.pipeline.slide_change_pilot import file_sha, object_sha, read_json, save_json
from backend.pipeline.transcription_vocabulary import asr_vocabulary_config, load_vocabulary, vocabulary_binding

SAMPLE_RATE = 16000
ASR_PROMPT = "這是以中文為主的聖經講課，可能夾雜英文及希臘文、希伯來文。中文使用繁體字，外文保留實際說出的語言。只轉錄聽到的話，不補上投影片、經文或推測的內容；背景雜音與靜音不是口述。"
ASR_CONFIG = {"model": "gpt-transcribe", "prompt": ASR_PROMPT,
              "extra_body": {"languages": ["zh-tw", "en", "el", "he"]}}


def read_audio(path: Path) -> np.ndarray:
    with wave.open(str(path)) as audio:
        if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (SAMPLE_RATE, 1, 2):
            raise ValueError("Audio must be 16kHz mono PCM16")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').copy()


def write_audio(path: Path, samples: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
        audio.writeframes(samples.astype('<i2').tobytes())


def quiet_boundaries(samples: np.ndarray, target_ms: int = 180000) -> list[dict]:
    # Float arithmetic avoids int16 square overflow. Quiet detection only chooses
    # chunk boundaries: no quiet intervals are removed from the recording.
    window = 1600
    floats = samples[:len(samples) // window * window].astype(np.float32) / 32768
    rms = np.sqrt(np.mean(floats.reshape(-1, window) ** 2, axis=1))
    threshold = max(float(np.quantile(rms, .15)) * 4, .003)
    voice_level = float(np.quantile(rms, .9))
    if voice_level > 0:
        # A recording without quiet frames must not label its steady loud signal
        # as silence merely because the noise percentile is also loud.
        threshold = min(threshold, voice_level * .25)
    quiet = rms < threshold
    runs, start = [], None
    for i, value in enumerate(quiet.tolist() + [False]):
        if value and start is None:
            start = i
        if not value and start is not None:
            if i - start >= 10:
                runs.append({'sample': (start + i) * window // 2,
                             'quiet_start_sample': start * window, 'quiet_end_sample': i * window})
            start = None
    result, cursor = [{'sample': 0, 'kind': 'window_start'}], 0
    target = target_ms * SAMPLE_RATE // 1000
    while len(samples) - cursor > target + SAMPLE_RATE * 20:
        ideal = cursor + target
        eligible = [r for r in runs if abs(r['sample'] - ideal) <= 20 * SAMPLE_RATE]
        if eligible:
            selected = min(eligible, key=lambda r: abs(r['sample'] - ideal))
            result.append({**selected, 'kind': 'energy_quiet_midpoint', 'rms_threshold': threshold})
        else:
            result.append({'sample': ideal, 'kind': 'hard_boundary_requires_review'})
        cursor = result[-1]['sample']
    return result + [{'sample': len(samples), 'kind': 'window_end'}]


def prepare(video: Path, out: Path, duration_ms: int = 900000) -> dict:
    if not 0 < duration_ms <= 900000:
        raise ValueError("This bounded pilot accepts at most 15 minutes")
    source_sha = file_sha(video)
    path = out / 'audio_manifest.json'
    if path.exists():
        saved = read_json(path)
        if saved['source_sha256'] != source_sha or saved['duration_ms'] != duration_ms:
            raise ValueError("Output belongs to a different video/window")
        for row in saved['chunks']:
            if file_sha(out / row['audio']) != row['audio_sha256']:
                raise ValueError("Saved audio chunk changed")
        if file_sha(out / 'audio.wav') != saved['audio_sha256']:
            raise ValueError("Master audio changed")
        return saved
    out.mkdir(parents=True, exist_ok=True)
    info = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
        'stream=index,codec_type,start_time,time_base,duration', '-of', 'json', str(video)],
        check=True, capture_output=True, text=True)
    import json
    streams = json.loads(info.stdout)['streams']
    video_stream = next(s for s in streams if s['codec_type'] == 'video')
    if abs(float(video_stream.get('start_time', '0'))) > .001:
        raise ValueError('Pilot requires a zero-origin video timeline; do not guess a media offset')
    # Retain original timestamps, then pad/trim against the original zero-based
    # movie timeline. Audio starts later are represented by leading silence.
    command = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-copyts', '-i', str(video),
        '-map', '0:a:0', '-af', 'aresample=16000:async=1:first_pts=0', '-t', str(duration_ms / 1000),
        '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(out / 'audio.wav')]
    subprocess.run(command, check=True)
    audio = read_audio(out / 'audio.wav')
    if len(audio) != duration_ms * SAMPLE_RATE // 1000:
        raise ValueError("Decoded audio does not cover the complete requested window")
    boundaries = quiet_boundaries(audio)
    chunks = []
    for i, (left, right) in enumerate(zip(boundaries, boundaries[1:])):
        begin, end = max(0, left['sample'] - 2 * SAMPLE_RATE), min(len(audio), right['sample'] + 2 * SAMPLE_RATE)
        audio_path = out / f'audio/chunk-{i:03d}.wav'
        write_audio(audio_path, audio[begin:end])
        chunks.append({'id': f'chunk-{i:03d}', 'audio': str(audio_path.relative_to(out)),
            'audio_sha256': file_sha(audio_path), 'sample_start': begin, 'sample_end': end,
            'core_start_sample': left['sample'], 'core_end_sample': right['sample'],
            'start_ms': begin * 1000 // SAMPLE_RATE, 'end_ms': end * 1000 // SAMPLE_RATE})
    result = {'schema_version': 'audio-transcript-pilot.v1', 'source_sha256': source_sha,
        'source_path': str(video.resolve()), 'duration_ms': duration_ms, 'sample_rate': SAMPLE_RATE,
        'audio_sha256': file_sha(out / 'audio.wav'), 'extraction_command': command, 'streams': streams,
        'boundaries': boundaries, 'chunks': chunks}
    save_json(path, result)
    return result


def saved_call(out: Path, request: dict, reserved_usd: float, invoke, *, max_calls: int = 16,
               budget_usd: float = 3.0) -> dict:
    folder = out / 'calls' / object_sha(request)
    state_path = folder / 'state.json'
    if state_path.exists():
        state = read_json(state_path)
        if read_json(folder / 'request.json') != request:
            raise ValueError('Saved model request changed')
        if state['status'] != 'complete':
            raise ValueError(f'Unresolved or rejected request at {folder}; never resend automatically')
        if file_sha(folder / 'response.json') != state['response_sha256']:
            raise ValueError('Saved model response changed')
        return read_json(folder / 'response.json')
    states = [read_json(p) for p in (out / 'calls').glob('*/state.json')]
    if any(s['status'] == 'sent_outcome_unknown' for s in states):
        raise ValueError('Previous call outcome unknown; reconcile before another model call')
    if len(states) >= max_calls or sum(s['reserved_usd'] for s in states) + reserved_usd > budget_usd:
        raise ValueError('Pilot call or cost reservation limit exhausted')
    save_json(folder / 'request.json', request)
    state = {'status': 'sent_outcome_unknown', 'reserved_usd': reserved_usd}
    save_json(state_path, state)
    started = time.perf_counter()
    try:
        result = invoke()
    except Exception as error:
        status = getattr(error, 'status_code', None) or getattr(error, 'code', None)
        state.update(http_status=status, error_type=type(error).__name__)
        if status in (400, 401, 402, 403, 404, 422, 429):
            state['status'] = 'rejected'
        save_json(state_path, state)
        raise
    save_json(folder / 'response.json', result)
    state.update(status='complete', response_sha256=file_sha(folder / 'response.json'),
                 wall_seconds=time.perf_counter() - started)
    save_json(state_path, state)
    return result


def transcribe(out: Path, *, vocabulary: dict | None = None) -> dict:
    config = asr_vocabulary_config(ASR_CONFIG, vocabulary)
    binding = {'vocabulary_hint': vocabulary_binding(vocabulary)} if vocabulary is not None else {}
    if (out / 'asr.json').exists():
        prior = read_json(out / 'asr.json')
        if prior['config'] != config or prior.get('vocabulary_hint') != binding.get('vocabulary_hint'):
            raise ValueError('ASR vocabulary/config changed; use a fresh output directory')
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv(Path(__file__).resolve().parents[2] / '.env')
    client = OpenAI(timeout=180, max_retries=0)
    manifest = read_json(out / 'audio_manifest.json')
    chunks = []
    for chunk in manifest['chunks']:
        path = out / chunk['audio']
        if file_sha(path) != chunk['audio_sha256']:
            raise ValueError('Chunk changed since preparation')
        request = {'stage': 'asr', 'config': config, **binding, 'audio_sha256': chunk['audio_sha256'],
                   'manifest_sha256': file_sha(out / 'audio_manifest.json'), 'chunk': chunk}
        def invoke():
            with path.open('rb') as stream:
                return client.audio.transcriptions.create(file=stream, **config).model_dump(mode='json')
        response = saved_call(out, request, (chunk['end_ms'] - chunk['start_ms']) / 60000 * .01, invoke)
        if not isinstance(response.get('text'), str):
            raise ValueError('ASR response has no transcript')
        chunks.append({**chunk, 'text': response['text'], 'request_sha256': object_sha(request),
                       'response_sha256': object_sha(response)})
        print(f'Transcribed {chunk["id"]}: {len(response["text"])} characters', flush=True)
    result = {'schema_version': 'audio-asr-pilot.v1', 'config': config, **binding,
              'manifest_sha256': file_sha(out / 'audio_manifest.json'), 'chunks': chunks,
              'estimated_usd': sum(c['end_ms'] - c['start_ms'] for c in chunks) / 60000 * .0045,
              'pricing_as_of': '2026-10-27', 'pricing_source': 'https://developers.openai.com/api/docs/pricing'}
    save_json(out / 'asr.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prepare', 'transcribe', 'align', 'format', 'assemble', 'visual', 'run'])
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--video', type=Path)
    parser.add_argument('--slides', type=Path)
    parser.add_argument('--vocabulary', type=Path, help='Optional versioned spelling hints')
    parser.add_argument('--aligner-python', type=Path, default=Path('.venv-align/bin/python'))
    parser.add_argument('--device', choices=['mps', 'cpu'], default='mps')
    parser.add_argument('--extract-visuals', action='store_true', help='After run, extract confirmed slide text and diagram primitives')
    parser.add_argument('--visual-provider', choices=['gemini', 'openai'], default='gemini')
    args = parser.parse_args()
    if args.stage in ('assemble', 'visual', 'run') and not args.slides:
        parser.error('assemble/visual/run requires --slides')
    if args.stage == 'run' and not (args.slides / 'report.json').is_file():
        parser.error('run requires an existing slide pilot report')
    if args.stage == 'visual':
        source = read_json(args.out / 'illustrated_transcript.json')
        if read_json(args.slides / 'report.json')['source_sha256'] != source['source_sha256']:
            raise ValueError('Slide report does not match assembled speech; no visual calls sent')
    if args.stage in ('prepare', 'run'):
        if not args.video:
            parser.error('prepare requires --video')
        result = prepare(args.video, args.out)
        if args.stage == 'run' and read_json(args.slides / 'report.json')['source_sha256'] != result['source_sha256']:
            raise ValueError('Slide report does not match source video; no ASR calls sent')
        print({'chunks': len(result['chunks']), 'duration_ms': result['duration_ms']})
    if args.stage in ('transcribe', 'run'):
        transcribe(args.out, vocabulary=load_vocabulary(args.vocabulary) if args.vocabulary else None)
    if args.stage in ('align', 'run'):
        env = dict(os.environ, PYTORCH_ENABLE_MPS_FALLBACK='1')
        # Resolving the venv python symlink launches the base interpreter and
        # loses its isolated packages. Keep the venv entry point intact.
        subprocess.run([str(args.aligner_python.absolute()), '-m', 'backend.pipeline.transcript_alignment_worker',
                        '--out', str(args.out.resolve()), '--device', args.device],
                       check=True, env=env, cwd=Path(__file__).resolve().parents[2])
    if args.stage in ('format', 'run'):
        from backend.pipeline.transcript_text_layout import stitch, format_text
        stitch(args.out)
        format_text(args.out)
    if args.stage in ('assemble', 'run'):
        from backend.pipeline.transcript_slide_assembly import assemble
        result = assemble(args.out, args.slides)
        print({'paragraphs': len(result['paragraphs']), 'slides': len(result['slides'])})
    if args.stage == 'visual' or args.stage == 'run' and args.extract_visuals:
        from backend.pipeline.slide_visual_evidence import extract
        from backend.pipeline.visual_transcript_packet import build
        extract(args.slides, args.out / 'visual-evidence', provider=args.visual_provider)
        print(build(args.out, args.out / 'visual-evidence', args.out / 'with-visual-evidence'))


if __name__ == '__main__':
    main()
