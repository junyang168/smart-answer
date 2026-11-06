"""Run in the pilot's isolated alignment venv, never the shared backend venv.

Unlike decode_forced_alignment(), this preserves raw model predictions without
the SDK's timestamp interpolation/repair. Invalid predictions remain unresolved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import wave

MODEL = 'Qwen/Qwen3-ForcedAligner-0.6B-hf'
REVISION = 'c07281df297b9905d24a508279258cccf987a064'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    os.replace(temp, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--device', choices=['cpu', 'mps'], default='mps')
    parser.add_argument('--decode-profile',choices=['raw','monotonic'],default='raw')
    args = parser.parse_args()
    import numpy as np
    import torch
    import transformers
    from transformers import AutoModelForTokenClassification, AutoProcessor
    out = args.out
    asr = json.loads((out / 'asr.json').read_text())
    config = {'model': MODEL, 'revision': REVISION, 'device': args.device,
              'dtype': 'float16' if args.device == 'mps' else 'float32',
              'torch': torch.__version__, 'transformers': transformers.__version__,
              'decode': 'raw argmax timestamp classes; no interpolation', 'language': 'Chinese'}
    if args.decode_profile=='monotonic':
        config['decode_profile']='monotonic-logit-path.v1'
    processor = model = None
    for chunk in asr['chunks']:
        audio_path = out / chunk['audio']
        if digest(audio_path) != chunk['audio_sha256']:
            raise ValueError('Audio changed before alignment')
        identity = {'config': config, 'text_sha256': hashlib.sha256(chunk['text'].encode()).hexdigest(),
                    'audio_sha256': digest(audio_path)}
        target = out / f'alignment/{chunk["id"]}.json'
        if target.exists():
            saved = json.loads(target.read_text())
            if saved['identity'] != identity:
                raise ValueError('Alignment output belongs to another text/audio/config')
            print(f'Reused {chunk["id"]}', flush=True)
            continue
        if model is None:
        processor = AutoProcessor.from_pretrained(MODEL, revision=REVISION)
        model = AutoModelForTokenClassification.from_pretrained(MODEL, revision=REVISION,
            dtype=torch.float16 if args.device == 'mps' else torch.float32, attn_implementation='sdpa').to(args.device).eval()
        with wave.open(str(audio_path)) as audio:
            if audio.getframerate() != 16000:
                raise ValueError('Expected 16kHz audio')
            samples = np.frombuffer(audio.readframes(audio.getnframes()), '<i2').astype(np.float32) / 32768
        started = time.perf_counter()
        inputs, word_lists = processor.prepare_forced_aligner_inputs(
            audio=samples, transcript=chunk['text'], language='Chinese', sampling_rate=16000)
        inputs = inputs.to(model.device, model.dtype)
        with torch.inference_mode():
            outputs = model(**inputs)
        if not torch.isfinite(outputs.logits).all():
            raise ValueError('Non-finite alignment logits; no invented timestamps')
        mask = inputs['input_ids'][0] == model.config.timestamp_token_id
        classes = outputs.logits.argmax(-1)[0][mask].cpu().tolist()
        if len(classes) != len(word_lists[0]) * 2:
            raise ValueError('Timestamp class count differs from prepared token count')
        segment_ms = processor.timestamp_segment_time
        items = [{'text': token, 'start_ms': round(classes[2*i] * segment_ms),
                  'end_ms': round(classes[2*i+1] * segment_ms)} for i, token in enumerate(word_lists[0])]
        decoded={}
        if args.decode_profile=='monotonic':
            from backend.pipeline.monotonic_acoustic_decode import monotonic_path
            matrix=outputs.logits[0][mask].detach().float().cpu().numpy()
            path,diagnostics=monotonic_path(matrix,int(len(samples)/16000*1000//segment_ms))
            decoded={'monotonic_items':[{'text':token,'start_ms':round(path[2*i]*segment_ms),'end_ms':round(path[2*i+1]*segment_ms)} for i,token in enumerate(word_lists[0])],
                     'monotonic_diagnostics':diagnostics}
        save(target, {**decoded,'schema_version': 'raw-forced-alignment.v1', 'identity': identity,
             'chunk_id': chunk['id'], 'items': items, 'timestamp_class_ids': classes,
             'timestamp_class_ms': segment_ms, 'wall_seconds': time.perf_counter() - started})
        del inputs, outputs
        if args.device == 'mps':
            torch.mps.empty_cache()
        print(f'Aligned {chunk["id"]}: {len(items)} units in {time.perf_counter()-started:.1f}s', flush=True)


def bounded_main():
    context = os.getenv('MEDIA_BATCH_CONTEXT')
    if not context:
        return main()
    from backend.pipeline.media_batch.runtime import slot, WaitingCapacity
    import time
    root = json.loads(context)['capacity_root']
    while True:
        try:
            with slot(root, 'mps', 1):
                return main()
        except WaitingCapacity:
            time.sleep(2)


if __name__ == '__main__':
    bounded_main()
