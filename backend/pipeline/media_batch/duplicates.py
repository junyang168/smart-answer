"""Conservative full-audio comparison for same-length lossy legacy derivatives.

Every voiced one-second interval must agree before automatic reuse. Partial
agreement is an unresolved identity, never permission to pay for another ASR.
This deliberately does not identify arbitrary cropped or speed-changed copies.
"""

from pathlib import Path
import subprocess
import numpy as np


def compare(left: Path, right: Path):
    command = ["ffmpeg", "-nostdin", "-v", "error", "-i"]
    tail = ["-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"]
    processes = [
        subprocess.Popen(
            command + [str(p)] + tail, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        for p in (left, right)
    ]
    matched = voiced = 0
    tail_difference = 0
    seconds = 0
    try:
        while True:
            raw = [p.stdout.read(16000) for p in processes]
            if not any(raw):
                break
            n = min(map(len, raw)) // 2
            tail_difference += abs(len(raw[0]) - len(raw[1])) // 2
            if n == 0:
                continue
            a, b = (
                np.frombuffer(r[: n * 2], dtype="<i2").astype(np.float32) for r in raw
            )
            a -= a.mean()
            b -= b.mean()
            na, nb = np.linalg.norm(a), np.linalg.norm(b)
            seconds += n / 8000
            if max(na, nb) / np.sqrt(n) < 40:
                continue
            voiced += 1
            correlation = float(np.dot(a, b) / (na * nb + 1e-12))
            if correlation >= 0.97:
                matched += 1
        if any(p.wait() != 0 for p in processes):
            raise ValueError("Duplicate comparison audio decode failed")
    finally:
        for p in processes:
            p.stdout.close()
            if p.poll() is None:
                p.kill()
                p.wait()
    return dict(
        policy="full-voiced-waveform-v1",
        matched_intervals=matched,
        voiced_intervals=voiced,
        compared_seconds=seconds,
        tail_difference_ms=tail_difference / 8,
        same=voiced >= 3 and matched == voiced and tail_difference <= 2000,
        uncertain=voiced > 0 and matched / voiced >= 0.4,
    )
