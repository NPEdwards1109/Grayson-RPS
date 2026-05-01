"""Short outcome tones played through the OS (reliable for local Streamlit)."""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import threading
import wave


def _chunk(freq: float, duration: float, sr: int, volume: float = 0.22) -> list[int]:
    n = max(int(sr * duration), 1)
    fade = max(int(sr * 0.008), 1)
    out: list[int] = []
    for i in range(n):
        env = min(1.0, i / fade) * min(1.0, (n - i) / max(fade * 2, 1))
        t = i / sr
        val = int(32767 * volume * env * math.sin(2 * math.pi * freq * t))
        out.append(max(-32767, min(32767, val)))
    return out


def _silence(duration: float, sr: int) -> list[int]:
    return [0] * max(int(sr * duration), 0)


def _samples_for_kind(kind: str, sr: int = 22050) -> list[int]:
    if kind == "win":
        return (
            _chunk(523.25, 0.1, sr)
            + _silence(0.02, sr)
            + _chunk(659.25, 0.1, sr)
            + _silence(0.02, sr)
            + _chunk(783.99, 0.16, sr)
        )
    if kind == "loss":
        return _chunk(185, 0.16, sr) + _silence(0.02, sr) + _chunk(110, 0.2, sr)
    return _chunk(392, 0.1, sr) + _silence(0.03, sr) + _chunk(392, 0.1, sr)


def _write_wav(path: str, samples: list[int], sr: int) -> None:
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(b"".join(struct.pack("<h", s) for s in samples))


def _play_path(path: str) -> None:
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["afplay", path],
                check=False,
                capture_output=True,
                timeout=30,
            )
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(path, winsound.SND_FILENAME)
        else:
            for cmd in (["aplay", "-q", path], ["paplay", path]):
                try:
                    subprocess.run(cmd, check=False, capture_output=True, timeout=30)
                    break
                except FileNotFoundError:
                    continue
    except (subprocess.TimeoutExpired, OSError):
        pass


def _play_thread(path: str) -> None:
    try:
        _play_path(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def play_outcome(kind: str) -> None:
    """Fire-and-forget: plays on the machine running Streamlit (your Mac speakers when local)."""
    if kind not in ("win", "loss", "tie"):
        return
    sr = 22050
    samples = _samples_for_kind(kind, sr)
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        _write_wav(path, samples, sr)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass
        return
    threading.Thread(target=_play_thread, args=(path,), daemon=True).start()
