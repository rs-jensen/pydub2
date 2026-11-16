import os
import shutil
import struct
import subprocess
import tempfile
import wave

import numpy as np
import pytest

data = os.path.join(os.path.dirname(__file__), "data")

notes = [220, 262, 330, 294, 247, 196, 349, 440]

# on and off times in seconds, picked so detect_silence gives the ranges test_pydub.py expects
bursts1 = [(0.4421, 3.2380, 0.6, 1), (3.8105, 5.5372, 0.02, 1.2), (5.7638, 99, 0.02, 1.08)]


def carrier(rate, n, ch):
    t = np.arange(n) / rate
    k = (t / 0.4).astype(int)
    p = 2 * np.pi * np.array(notes)[k % len(notes)] * (t - k * 0.4)
    return np.sqrt(2) * np.sin(p + ch * np.pi / 2)


def burst(t, on, off, fade, gain):
    up = np.clip((t - on) / 0.02, 0, 1)
    down = np.where(t < off, 1.0, np.where(t < off + fade, 0.5 * (1 + np.cos(np.pi * (t - off) / fade)), 0.0))
    return up * down * gain


def music(rate, secs, ch, level, spans=None):
    n = round(rate * secs)
    t = np.arange(n) / rate
    if spans is None:
        env = 0.7 + 0.3 * np.sin(2 * np.pi * 0.7 * t)
    else:
        env = sum(burst(t, *b) for b in spans)
    out = np.empty((n, ch))
    for c in range(ch):
        out[:, c] = carrier(rate, n, c) * env * level
    return out


def pcm16(x):
    return np.round(x * 32768).clip(-32768, 32767).astype("<i2")


def write_wav(path, rate, width, ch, buf):
    w = wave.open(path, "wb")
    w.setnchannels(ch)
    w.setsampwidth(width)
    w.setframerate(rate)
    w.writeframes(buf)
    w.close()


def ff(*args):
    p = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr)


def make():
    os.makedirs(data, exist_ok=True)

    def out(name):
        return os.path.join(data, name)

    test1 = pcm16(music(32000, 10.01, 2, 0.125, bursts1))
    write_wav(out("test1.wav"), 32000, 2, 2, test1.tobytes())
    with open(out("test1.raw"), "wb") as f:
        f.write(test1.tobytes())
    write_wav(out("test1-8bit.wav"), 32000, 1, 2, ((test1 >> 8) + 128).astype("u1").tobytes())

    x24 = np.round(music(44100, 2, 2, 0.1) * 8388608).clip(-8388608, 8388607).astype("<i4")
    write_wav(out("test1-24bit.wav"), 44100, 3, 2, x24.view("u1").reshape(-1, 4)[:, :3].tobytes())

    with open(out("test1_empty.wav"), "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16))
        f.write(b"data" + struct.pack("<I", 0))

    dc = music(22050, 10, 2, 0.12)
    dc = dc - dc.mean(axis=0) + [-0.16, 0.1]
    write_wav(out("test-dc_offset.wav"), 22050, 2, 2, pcm16(dc).tobytes())

    write_wav(out("test4.wav"), 48000, 2, 1, pcm16(music(48000, 8, 1, 0.1, [(0, 3, 0.6, 1), (4.5, 99, 0.02, 1)])).tobytes())

    with tempfile.TemporaryDirectory() as tmp:
        def src(name, rate, secs, ch, level):
            path = os.path.join(tmp, name)
            write_wav(path, rate, 2, ch, pcm16(music(rate, secs, ch, level)).tobytes())
            return path

        ff("-i", out("test1.wav"), "-b:a", "96k", out("test1.mp3"))
        ff("-i", src("a.wav", 44100, 5.04, 2, 0.134), "-b:a", "128k", out("test2.mp3"))
        ff("-i", src("b.wav", 44100, 8.33, 1, 0.32), "-b:a", "64k", out("test3.mp3"))
        party = src("c.wav", 44100, 6.58, 1, 0.1)
        ff("-i", party, "-b:a", "64k", out("party.mp3"))
        ff("-i", src("d.wav", 44100, 3.05, 1, 0.1), "-c:a", "flac", "-f", "ogg", out("bach.ogg"))
        ff("-i", src("e.wav", 44100, 9.66, 1, 0.1), "-b:a", "64k", out("format_test.m4a"))
        shutil.copy(out("format_test.m4a"), out("wrong_extension.aac"))
        ff("-i", src("f.wav", 48000, 1.5, 2, 0.1), "-c:a", "libopus", out("test5.webm"))
        ff("-f", "lavfi", "-i", "color=c=gray:s=64x48:r=10:d=2.27", "-i", src("g.wav", 48000, 2.27, 2, 0.1),
           "-c:v", "mpeg4", "-c:a", "aac", "-shortest", out("creative_common.mp4"))
        hi = src("h.wav", 192000, 0.25, 2, 0.1)
        shutil.copy(hi, out("test-192khz-16bit.wav"))
        for name, codec in (("24bit.wav", "pcm_s24le"), ("32bit.wav", "pcm_s32le"), ("64bit.wav", "pcm_f64le")):
            ff("-i", hi, "-c:a", codec, out("test-192khz-" + name))
        ff("-i", hi, "-c:a", "flac", "-sample_fmt", "s32", out("test-192khz-32bit.flac"))
        ff("-f", "lavfi", "-i", "testsrc2=s=200x200", "-frames:v", "1", out("cover.jpg"))
        ff("-f", "lavfi", "-i", "testsrc2=s=200x200", "-frames:v", "1", out("cover.png"))


@pytest.fixture(scope="session", autouse=True)
def audio():
    if not os.path.exists(os.path.join(data, "test-192khz-64bit.wav")):
        make()
