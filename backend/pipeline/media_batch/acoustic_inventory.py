"""Local, source-bound Chromaprint inventory. No network or model calls.

Fingerprints retrieve candidates; they never by themselves authorize reuse.
The native library is optional and uses the existing FFmpeg decoder.
"""

import ctypes as C
import ctypes.util
import json
import subprocess
from pathlib import Path
import numpy as np
from .runtime import atomic, file_sha


def library():
    name = ctypes.util.find_library("chromaprint")
    if not name:
        candidate = Path("/opt/homebrew/opt/chromaprint/lib/libchromaprint.dylib")
        name = str(candidate) if candidate.exists() else None
    if not name:
        raise RuntimeError(
            "Install the Chromaprint native library for local acoustic inventory"
        )
    lib = C.CDLL(name)
    lib.chromaprint_new.argtypes = [C.c_int]
    lib.chromaprint_new.restype = C.c_void_p
    lib.chromaprint_start.argtypes = [C.c_void_p, C.c_int, C.c_int]
    lib.chromaprint_feed.argtypes = [C.c_void_p, C.POINTER(C.c_int16), C.c_int]
    lib.chromaprint_finish.argtypes = [C.c_void_p]
    lib.chromaprint_get_raw_fingerprint.argtypes = [
        C.c_void_p,
        C.POINTER(C.POINTER(C.c_uint32)),
        C.POINTER(C.c_int),
    ]
    lib.chromaprint_get_item_duration_ms.argtypes = [C.c_void_p]
    lib.chromaprint_get_item_duration.argtypes = [C.c_void_p]
    lib.chromaprint_get_sample_rate.argtypes = [C.c_void_p]
    lib.chromaprint_get_delay_ms.argtypes = [C.c_void_p]
    lib.chromaprint_free.argtypes = [C.c_void_p]
    lib.chromaprint_dealloc.argtypes = [C.c_void_p]
    lib.chromaprint_get_version.restype = C.c_char_p
    return lib


def fingerprint(path, cache):
    path = Path(path)
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    before = path.stat()
    sha = file_sha(path)
    import fcntl

    with (cache / (sha + ".lock")).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _fingerprint(path, cache, sha, before)


def _fingerprint(path, cache, sha, before):
    stem = cache / sha
    meta = stem.with_suffix(".json")
    array = stem.with_suffix(".npy")
    if meta.exists():
        saved = json.loads(meta.read_text())
        if (
            saved["source_sha256"] != sha
            or file_sha(array) != saved["fingerprint_sha256"]
        ):
            raise ValueError("Fingerprint cache changed")
        # Algorithm 1 advances 1365 samples at 11025 Hz. Older cache
        # metadata used the native integer-millisecond accessor (123 ms),
        # whose truncation accumulates over long recordings. Arrays stay valid.
        if saved.get("algorithm") == 1:
            saved["step_ms"] = 1000 * 1365 / 11025
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Source changed while fingerprinting")
        return saved
    lib = library()
    ctx = lib.chromaprint_new(1)
    if not ctx or not lib.chromaprint_start(ctx, 11025, 1):
        raise ValueError("Cannot start audio fingerprint")
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "11025",
            "-f",
            "s16le",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    size = 0
    pointer = C.POINTER(C.c_uint32)()
    try:
        while raw := process.stdout.read(11025 * 2 * 20):
            if len(raw) % 2:
                raise ValueError("Truncated PCM sample")
            pcm = (C.c_int16 * (len(raw) // 2)).from_buffer_copy(raw)
            if not lib.chromaprint_feed(ctx, pcm, len(raw) // 2):
                raise ValueError("Fingerprint feed failed")
            size += len(raw) // 2
        if process.wait() != 0 or not size:
            raise ValueError("Audio decode failed")
        count = C.c_int()
        if not lib.chromaprint_finish(ctx) or not lib.chromaprint_get_raw_fingerprint(
            ctx, C.byref(pointer), C.byref(count)
        ):
            raise ValueError("Fingerprint finalization failed")
        hashes = np.ctypeslib.as_array(pointer, shape=(count.value,)).copy()
        saved = dict(
            schema_version="local-chromaprint-inventory.v1",
            source_sha256=sha,
            bytes=before.st_size,
            decoded_seconds=size / 11025,
            fingerprint_count=count.value,
            step_ms=1000
            * lib.chromaprint_get_item_duration(ctx)
            / lib.chromaprint_get_sample_rate(ctx),
            delay_ms=lib.chromaprint_get_delay_ms(ctx),
            chromaprint_version=lib.chromaprint_get_version().decode(),
            algorithm=1,
        )
    finally:
        if pointer:
            lib.chromaprint_dealloc(pointer)
        lib.chromaprint_free(ctx)
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Source changed while fingerprinting")
    # Workers can see the same byte-identical source. A single writer per SHA is
    # selected by the caller; all other rows reuse that result.
    np.save(array, hashes, allow_pickle=False)
    saved["fingerprint_sha256"] = file_sha(array)
    atomic(meta, saved)
    return saved


def find_candidates(query, references, *, max_probes=700):
    """Retrieve offset-consistent candidates from complete fingerprints.

    References are (id, uint32 array). This is a retrieval score only; unrelated
    speech, music and repeated intros must be rejected by waveform verification.
    """
    from collections import Counter

    if len(query) < 100:
        return []
    # Building these once is supported by CandidateIndex for a whole inventory.
    return CandidateIndex(references).find(query, max_probes=max_probes)


class CandidateIndex:
    def __init__(self, references):
        self.references = references
        words = []
        ids = []
        positions = []
        for rid, (_, fp) in enumerate(references):
            pos = np.arange(0, len(fp), dtype=np.int32)
            words.append(fp[pos])
            positions.append(pos)
            ids.append(np.full(len(pos), rid, np.int32))
        self.words = np.concatenate(words) if words else np.array([], np.uint32)
        self.ids = np.concatenate(ids) if ids else np.array([], np.int32)
        self.positions = (
            np.concatenate(positions) if positions else np.array([], np.int32)
        )
        self.lookups = []
        for shift in (0, 16):
            keys = (self.words >> shift) & 65535
            order = np.argsort(keys)
            self.lookups.append((shift, keys[order], order))

    def find(self, query, max_probes=700):
        from collections import Counter

        votes = Counter()
        probes = np.unique(
            np.linspace(
                20, max(20, len(query) - 21), min(max_probes, max(0, len(query) - 40))
            ).astype(int)
        )
        for shift, keys, order in self.lookups:
            for qpos in probes:
                key = np.uint32((query[qpos] >> shift) & 65535)
                low, high = np.searchsorted(keys, key, "left"), np.searchsorted(
                    keys, key, "right"
                )
                if high - low > 150 or high == low:
                    continue
                hits = order[low:high]
                offsets = np.floor_divide(self.positions[hits] - qpos, 16)
                packed = self.ids[hits].astype(np.int64) * 1000000 + offsets + 500000
                values, counts = np.unique(packed, return_counts=True)
                votes.update(dict(zip(map(int, values), map(int, counts))))
        per_ref = {}
        for code, vote in votes.most_common(100):
            if vote < 8:
                break
            rid = code // 1000000
            bucket = (code % 1000000) - 500000
            if rid not in per_ref:
                per_ref[rid] = (bucket, vote)
            if len(per_ref) >= 16:
                break
        pop = np.array([i.bit_count() for i in range(256)], dtype=np.uint8)
        candidates = []
        for rid, (bucket, vote) in per_ref.items():
            name, ref = self.references[rid]
            best = None
            for offset in range(bucket * 16 - 16, bucket * 16 + 33):
                start = max(0, -offset)
                end = min(len(query), len(ref) - offset)
                if end - start < 480:
                    continue
                q = np.arange(start, end, 8)
                xor = np.bitwise_xor(query[q], ref[q + offset])
                errors = pop[xor.view(np.uint8).reshape(-1, 4)].sum(axis=1) / 32
                # Require a sustained minute, not a repeated short greeting.
                width = min(len(errors), 60)
                cumulative = np.r_[0, np.cumsum(errors)]
                sums = (cumulative[width:] - cumulative[:-width]) / width
                index = int(np.argmin(sums))
                score = 1 - float(sums[index])
                if best is None or score > best["score"]:
                    best = dict(
                        reference=name,
                        score=score,
                        offset_frames=offset,
                        votes=vote,
                        overlap_frames=end - start,
                        query_start_frame=start,
                        query_end_frame=end,
                        strongest_start_frame=int(q[index]),
                        strongest_end_frame=int(q[index + width - 1]),
                    )
            if best and best["score"] >= 0.82:
                candidates.append(best)
        return sorted(candidates, key=lambda x: (x["score"], x["votes"]), reverse=True)[
            :8
        ]


def decode_clip(path, start, seconds, rate=8000):
    # Compressed/ADPCM fast seeks can drift from the decoded sample clock.
    # Trim canonical samples after resampling instead of trusting seek PTS.
    first = round(max(0, start) * rate)
    last = first + round(seconds * rate)
    filters = f"aresample={rate},atrim=start_sample={first}:end_sample={last},asetpts=PTS-STARTPTS"
    raw = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            filters,
            "-t",
            str(seconds),
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype="<f4")


def verify_anchors(
    query, reference, offset, query_duration, reference_duration, *, anchor_count=9
):
    """Independent waveform/clock check before any reuse decision."""
    start = max(0, -offset) + 4
    end = min(query_duration, reference_duration - offset) - 16
    if end - start < 30:
        return dict(verified=False, reason="overlap too short", anchors=[])
    anchors = []
    for at in np.linspace(start, end, anchor_count):
        a = decode_clip(query, float(at), 12).astype(np.float64)
        rstart = max(0, float(at) + offset - 3)
        b = decode_clip(reference, rstart, 18).astype(np.float64)
        if len(a) < 64000 or len(b) < len(a) or np.std(a) < 0.0008:
            anchors.append(dict(query_seconds=float(at), silent_or_short=True))
            continue
        a -= a.mean()
        b -= b.mean()
        n = len(a)
        m = len(b) - n + 1
        size = 1 << (len(a) + len(b) - 1).bit_length()
        dots = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)[
            :m
        ]
        squares = np.r_[0, np.cumsum(b * b)]
        energy = squares[n:] - squares[:-n]
        scores = dots / (np.linalg.norm(a) * np.sqrt(np.maximum(energy, 1e-20)))
        pos = int(np.argmax(scores))
        anchors.append(
            dict(
                query_seconds=float(at),
                correlation=float(scores[pos]),
                offset_seconds=rstart + pos / 8000 - float(at),
            )
        )
    voiced = [a for a in anchors if "correlation" in a]
    verified = (
        len(voiced) >= min(7, anchor_count)
        and min(a["correlation"] for a in voiced) >= 0.97
        and np.ptp([a["offset_seconds"] for a in voiced]) <= 0.035
    )
    return dict(
        verified=bool(verified),
        anchors=anchors,
        offset_seconds=(
            float(np.median([a["offset_seconds"] for a in voiced])) if voiced else None
        ),
        overlap_start_seconds=max(0, -offset),
        overlap_end_seconds=min(query_duration, reference_duration - offset),
    )


def verify_continuous(query, reference, offset, query_duration, reference_duration):
    """Check every voiced second over the entire common recording, not anchors.

    Additional unmatched source speech keeps the result partial and held.
    """
    qstart = max(0, -offset)
    rstart = max(0, offset)
    duration = min(query_duration - qstart, reference_duration - rstart)
    if duration < 60:
        return dict(same=False, reason="overlap under one minute")

    def process(path, start):
        return subprocess.Popen(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-af",
                f"aresample=8000,atrim=start_sample={round(start*8000)}:end_sample={round((start+duration)*8000)},asetpts=PTS-STARTPTS",
                "-t",
                str(duration),
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "s16le",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    streams = [process(query, qstart), process(reference, rstart)]
    bad = []
    voiced = matched = 0
    seconds = 0
    tail = 0
    try:
        while True:
            raw = [p.stdout.read(16000) for p in streams]
            if not any(raw):
                break
            n = min(map(len, raw)) // 2
            tail += abs(len(raw[0]) - len(raw[1])) // 2
            if not n:
                continue
            a, b = (
                np.frombuffer(r[: n * 2], dtype="<i2").astype(np.float64) for r in raw
            )
            a -= a.mean()
            b -= b.mean()
            norms = np.linalg.norm(a), np.linalg.norm(b)
            if max(norms) / np.sqrt(n) >= 40:
                voiced += 1
                score = float(np.dot(a, b) / (norms[0] * norms[1] + 1e-20))
                if score >= 0.95:
                    matched += 1
                else:
                    bad.append(
                        dict(query_start_seconds=qstart + seconds, correlation=score)
                    )
            seconds += n / 8000
        if any(p.wait() != 0 for p in streams):
            raise ValueError("Continuous identity decode failed")
    finally:
        for p in streams:
            p.stdout.close()
            if p.poll() is None:
                p.kill()
                p.wait()
    full = voiced >= 30 and matched == voiced and tail <= 2000
    return dict(
        same=bool(full and qstart <= 1 and query_duration - qstart - duration <= 1),
        common_audio_verified=bool(full),
        compared_seconds=seconds,
        voiced_seconds=voiced,
        matched_seconds=matched,
        mismatch_count=len(bad),
        mismatch_examples=bad[:12],
        tail_difference_ms=tail / 8,
        unmatched_query_prefix_seconds=qstart,
        unmatched_query_tail_seconds=max(0, query_duration - qstart - duration),
    )
