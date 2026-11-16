import json
import os
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import wave

SR = 44100
EXTS = ["wav", "flac", "mp3", "ogg", "opus"]
LAYOUTS = ["s44"]
ENCODE = {"flac": ["-c:a", "flac"], "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
          "ogg": ["-c:a", "libvorbis", "-q:a", "4"], "opus": ["-c:a", "libopus"]}


def syllable(rng, n, f0):
    import numpy as np
    ph = np.cumsum(f0 * np.linspace(1, rng.uniform(0.9, 1.1), n) / SR)
    src = ph % 1.0 - 0.5
    if rng.random() < 0.15:
        src = np.diff(rng.standard_normal(n + 1))
    fr = np.fft.rfftfreq(n, 1 / SR)
    peaks = rng.uniform(300, 800), rng.uniform(900, 2400), rng.uniform(2500, 3300)
    h = sum(a * np.exp(-0.5 * ((fr - p) / w) ** 2) for a, p, w in zip((1, 0.6, 0.3), peaks, (80, 150, 250)))
    y = np.fft.irfft(np.fft.rfft(src) * (h + 0.05) / (1 + (fr / 3000) ** 2), n)
    return (y * np.hanning(n) ** 0.7).astype(np.float32)


def speech(rng):
    import numpy as np
    n = 60 * SR
    x = np.zeros(n, np.float32)
    pos = int(rng.uniform(0.2, 1.0) * SR)
    while pos < n - 5 * SR:
        end = pos + int(rng.uniform(1.0, 4.0) * SR)
        f0 = rng.uniform(90, 220)
        while pos < end:
            m = int(rng.uniform(0.12, 0.28) * SR)
            x[pos:pos + m] += syllable(rng, m, f0)
            f0 = min(max(f0 * rng.uniform(0.9, 1.1), 80), 260)
            pos += m
        pos += int(rng.uniform(1.2, 2.5) * SR) if rng.random() < 0.3 else int(rng.uniform(0.1, 0.5) * SR)
    on = np.abs(x) > 0.01
    x *= 0.1 / np.sqrt(np.mean(x[on] ** 2))
    x += rng.standard_normal(n).astype(np.float32) * 0.0005
    r = 0.8 * x + rng.standard_normal(n).astype(np.float32) * 0.0005
    return np.clip(np.stack([x, r], 1), -0.95, 0.95)


def music(rng):
    import numpy as np
    n = 60 * SR
    out = np.zeros((n, 2), np.float32)

    def add(start, y, pan):
        y = y[:n - start]
        out[start:start + len(y), 0] += y * (1 - pan)
        out[start:start + len(y), 1] += y * pan

    def note(start, dur, midi, amp, pan, decay):
        t = np.arange(int(dur * SR), dtype=np.float32) / SR
        f = 440 * 2 ** ((midi - 69) / 12)
        y = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 6))
        add(start, y * np.exp(-decay * t) * np.minimum(t / 0.01, 1) * amp, pan)

    bar = 2 * SR
    for b in range(30):
        root = [45, 48, 50, 43][int(rng.random() * 4)]
        s = b * bar
        note(s, 2.0, root, 0.3, 0.5, 1.0)
        for i, iv in enumerate((12, 15 + int(rng.random() * 2), 19)):
            note(s, 2.0, root + iv, 0.12, 0.3 + 0.2 * i, 0.5)
        for e in range(8):
            if rng.random() < 0.7:
                note(s + e * bar // 8, 0.25 + 0.5 * rng.random(), root + 24 + [0, 3, 5, 7, 10][int(rng.random() * 5)], 0.2, 0.5 + 0.3 * rng.random(), 3.0)
            t = np.arange(int(0.08 * SR), dtype=np.float32) / SR
            add(s + e * bar // 8, np.diff(rng.standard_normal(len(t) + 1)).astype(np.float32) * np.exp(-60 * t) * 0.1, 0.6)
        for q in range(4):
            t = np.arange(int(0.3 * SR), dtype=np.float32) / SR
            add(s + q * bar // 4, np.sin(2 * np.pi * 60 * t) * np.exp(-12 * t) * 0.8, 0.5)
    out *= 0.12 / np.sqrt(np.mean(out ** 2))
    return np.clip(out, -0.95, 0.95)


def ff(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error", *args], check=True)


def gen(data):
    import numpy as np
    os.makedirs(data, exist_ok=True)
    for kind, fn in (("speech", speech), ("music", music)):
        full = f"{data}/{kind}_10m_s44.wav"
        if not os.path.exists(full):
            w = wave.open(full + ".part", "wb")
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            for i in range(10):
                x = fn(np.random.default_rng(i * 2 + (kind == "music")))
                w.writeframes((x * 32767).astype("<i2").tobytes())
            w.close()
            os.rename(full + ".part", full)
        for dur, secs in (("60s", "60"), ("5s", "5")):
            ff("-i", full, "-t", secs, "-c", "copy", f"{data}/{kind}_{dur}_s44.wav")
        for dur in ("5s", "60s", "10m"):
            ff("-i", f"{data}/{kind}_{dur}_s44.wav", "-ac", "1", "-ar", "16000", f"{data}/{kind}_{dur}_m16.wav")
            for lay in LAYOUTS:
                base = f"{data}/{kind}_{dur}_{lay}"
                for ext, args in ENCODE.items():
                    if ext == "opus" and lay == "m16":
                        args = args + ["-b:a", "48k"]
                    elif ext == "opus":
                        args = args + ["-b:a", "96k"]
                    if not os.path.exists(f"{base}.{ext}"):
                        ff("-i", f"{base}.wav", *args, f"{base}.{ext}")



PYDUBS = ["pydub", "pydublts", "pydub2"]
LOADERS = PYDUBS + ["sf", "av", "mini", "librosa", "audioread"]
VENV = {"audioread": "librosa", "silero": "vad"}
IMPORT = {"pydub": "from pydub import AudioSegment", "pydublts": "from pydub import AudioSegment",
          "pydub2": "from pydub2 import AudioSegment", "pydub2mp3": "from pydub2 import AudioSegment",
          "sf": "import soundfile", "av": "import av", "mini": "import miniaudio",
          "librosa": "import librosa", "audioread": "import audioread", "silero": "import silero_vad"}
SECONDS = {"5s": 5, "60s": 60, "10m": 600}
R = {}


class Unsupported(Exception):
    pass


def peak():
    div = 2 ** 20 if sys.platform == "darwin" else 1024
    return (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss + resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss) / div


def cpu():
    s, c = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return s.ru_utime + s.ru_stime + c.ru_utime + c.ru_stime


def timed(f):
    R["base"] = peak()
    c0, t0 = cpu(), time.perf_counter()
    r = f()
    R["wall"] = time.perf_counter() - t0
    R["cpu"] = cpu() - c0
    R["peak"] = peak()
    return r


def P(name):
    return os.path.join(os.environ["BENCH_DATA"], name)


def read_wav(name):
    with wave.open(P(name)) as w:
        return w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()


def modname(lib):
    return "pydub2" if lib.startswith("pydub2") else "pydub"


def sub(lib, name):
    import importlib
    return importlib.import_module(modname(lib) + "." + name)


def seg_of(lib, name):
    raw, rate, ch = read_wav(name)
    return sub(lib, "audio_segment").AudioSegment(data=raw, sample_width=2, frame_rate=rate, channels=ch)


def chk(x, rate=None, ch=None):
    import numpy as np
    if hasattr(x, "raw_data"):
        rate, ch, x = x.frame_rate, x.channels, np.frombuffer(x.raw_data, {1: np.int8, 2: np.int16, 4: np.int32}[x.sample_width])
    elif isinstance(x, (bytes, bytearray)):
        x = np.frombuffer(x, np.int16)
    x = np.asarray(x)
    m = np.abs(x.reshape(-1)[::97].astype(np.float32))
    full = 2 ** (8 * x.dtype.itemsize - 1) if x.dtype.kind == "i" else 1
    return {"n": x.size // ch, "rate": rate, "ch": ch, "m": round(float(m.mean()) / full, 5), "bits": 8 * x.dtype.itemsize}


def av_decode(path, start=None, dur=None):
    import av
    import numpy as np
    c = av.open(path)
    s = c.streams.audio[0]
    ch, rate = len(s.layout.channels), s.rate
    rs = av.AudioResampler(format="s16", layout=s.layout, rate=rate)
    if start is not None:
        c.seek(int(start * 1000000))
    parts, t0, got = [], None, 0
    for f in c.decode(s):
        if t0 is None:
            t0 = f.time
        for g in rs.resample(f):
            parts.append(g.to_ndarray().reshape(-1))
            got += parts[-1].size
        if start is not None and got >= (start - t0 + dur) * rate * ch:
            break
    else:
        for g in rs.resample(None):
            parts.append(g.to_ndarray().reshape(-1))
    x = np.concatenate(parts)
    if start is not None:
        skip = max(int((start - t0) * rate), 0) * ch
        x = x[skip:skip + int(dur * rate) * ch]
    return x.reshape(-1, ch), rate, ch


def decode(lib, path, start=None, dur=None):
    import numpy as np
    if lib.startswith("pydub"):
        kw = {} if start is None else {"start_second": start, "duration": dur}
        return sub(lib, "audio_segment").AudioSegment.from_file(path, **kw), None, None
    if lib == "sf":
        import soundfile as sf
        with sf.SoundFile(path) as f:
            if start is not None:
                f.seek(int(start * f.samplerate))
            x = f.read(-1 if start is None else int(dur * f.samplerate), dtype="int16", always_2d=True)
            return x, f.samplerate, f.channels
    if lib == "av":
        return av_decode(path, start, dur)
    if lib == "mini":
        import miniaudio as m
        if path.endswith(".opus"):
            raise Unsupported(lib)
        i = m.get_file_info(path)
        if start is None:
            d = m.decode_file(path, m.SampleFormat.SIGNED16, i.nchannels, i.sample_rate)
            return d.samples, i.sample_rate, i.nchannels
        g = m.stream_file(path, m.SampleFormat.SIGNED16, i.nchannels, i.sample_rate, i.sample_rate, seek_frame=int(start * i.sample_rate))
        x = np.concatenate([np.asarray(next(g)) for _ in range(int(dur))])
        return x, i.sample_rate, i.nchannels
    if lib == "librosa":
        import librosa
        y, sr = librosa.load(path, sr=None, mono=False, offset=start or 0.0, duration=dur)
        return y, sr, 1 if y.ndim == 1 else y.shape[0]
    if lib == "audioread":
        import audioread
        with audioread.audio_open(path) as f:
            R["backend"] = type(f).__name__
            return b"".join(f), f.samplerate, f.channels
    raise Unsupported(lib)


def op_none(lib, a):
    timed(lambda: None)


def op_import(lib, a):
    timed(lambda: exec(IMPORT[lib], {}))


def op_load(lib, a):
    x, rate, ch = timed(lambda: decode(lib, P(a[0])))
    R["check"] = chk(x, rate, ch)


def op_load10(lib, a):
    start = (SECONDS[a[0].split("_")[1]] - 10) / 2
    x, rate, ch = timed(lambda: decode(lib, P(a[0]), start, 10))
    R["check"] = chk(x, rate, ch)


def av_encode(path, ext, x, rate, ch):
    import av
    codec = {"wav": "pcm_s16le", "flac": "flac", "ogg": "libvorbis", "opus": "libopus", "mp3": "libmp3lame"}[ext]
    layout = "stereo" if ch == 2 else "mono"
    o = av.open(path, "w")
    st = o.add_stream(codec, rate=48000 if ext == "opus" else rate, layout=layout)
    if ext == "mp3":
        st.bit_rate = 128000
    for i in range(0, len(x), rate):
        f = av.AudioFrame.from_ndarray(x[i:i + rate].reshape(1, -1), format="s16", layout=layout)
        f.rate = rate
        for p in st.encode(f):
            o.mux(p)
    for p in st.encode(None):
        o.mux(p)
    o.close()


def op_export(lib, a):
    import numpy as np
    fmt = a[0]
    raw, rate, ch = read_wav(a[1])
    out = os.path.join(os.environ.get("BENCH_TMP", tempfile.gettempdir()), "bench_out_%d.%s" % (os.getpid(), fmt))
    if lib.startswith("pydub"):
        seg = sub(lib, "audio_segment").AudioSegment(data=raw, sample_width=2, frame_rate=rate, channels=ch)
        timed(lambda: seg.export(out, format=fmt, bitrate="128k" if fmt == "mp3" else None))
    else:
        x = np.frombuffer(raw, np.int16).reshape(-1, ch)
        if lib == "sf" and fmt == "opus" and rate != 48000:
            raise Unsupported(lib)
        if lib == "sf":
            import soundfile as sf
            f, s = {"wav": ("WAV", None), "flac": ("FLAC", None), "ogg": ("OGG", "VORBIS"), "opus": ("OGG", "OPUS"), "mp3": ("MP3", None)}[fmt]
            timed(lambda: sf.write(out, x, rate, format=f, subtype=s))
        elif lib == "av" and fmt != "ogg":
            timed(lambda: av_encode(out, fmt, x, rate, ch))
        elif lib == "mini" and fmt == "wav":
            import array
            import miniaudio as m
            buf = array.array("h")
            buf.frombytes(raw)
            timed(lambda: m.wav_write_file(out, m.DecodedSoundFile("x", ch, rate, m.SampleFormat.SIGNED16, buf)))
        else:
            raise Unsupported(lib)
    R["check"] = {"bytes": os.path.getsize(out)}
    os.remove(out)


def op_resample(lib, a):
    import numpy as np
    q, name = a
    raw, rate, ch = read_wav(name)
    if lib.startswith("pydub"):
        seg = seg_of(lib, name)
        kw = {"resample_quality": "high"} if q == "high" else {}
        r = timed(lambda: seg.set_frame_rate(16000, **kw))
        R["check"] = chk(r)
    elif lib == "librosa":
        import librosa
        y = (np.frombuffer(raw, np.int16).astype(np.float32) / 32768).reshape(-1, ch).T.copy()
        r = timed(lambda: librosa.resample(y, orig_sr=rate, target_sr=16000, res_type="soxr_hq" if q == "high" else "soxr_qq"))
        R["check"] = chk(r, 16000, ch)
    elif lib == "av" and q == "default":
        import av
        layout = "stereo" if ch == 2 else "mono"
        x = np.frombuffer(raw, np.int16).reshape(-1, ch)
        frames = []
        for i in range(0, len(x), rate):
            f = av.AudioFrame.from_ndarray(x[i:i + rate].reshape(1, -1), format="s16", layout=layout)
            f.rate = rate
            frames.append(f)

        def go():
            rs = av.AudioResampler(format="s16", layout=layout, rate=16000)
            out = [g.to_ndarray().reshape(-1) for f in frames for g in rs.resample(f)]
            return np.concatenate(out + [g.to_ndarray().reshape(-1) for g in rs.resample(None)])
        R["check"] = chk(timed(go), 16000, ch)
    elif lib == "mini" and q == "default":
        import miniaudio as m
        S = m.SampleFormat.SIGNED16
        r = timed(lambda: m.convert_frames(S, ch, rate, raw, S, ch, 16000))
        R["check"] = chk(bytes(r), 16000, ch)
    else:
        raise Unsupported(lib)


def op_gradio_in(lib, a):
    import numpy as np
    A = sub(lib, "audio_segment").AudioSegment

    def go():
        s = A.from_file(P(a[0]))
        return np.array(s.get_array_of_samples()).reshape(-1, s.channels), s.frame_rate, s.channels
    R["check"] = chk(*timed(go))


def op_gradio_in_fast(lib, a):
    import numpy
    A = sub(lib, "audio_segment").AudioSegment

    def go():
        s = A.from_file(P(a[0]))
        return s.to_numpy(), s.frame_rate, s.channels
    R["check"] = chk(*timed(go))


def op_gradio_out(lib, a):
    import numpy as np
    fmt = a[0]
    raw, rate, ch = read_wav(a[1])
    x = np.frombuffer(raw, np.int16).reshape(-1, ch)
    A = sub(lib, "audio_segment").AudioSegment
    out = os.path.join(os.environ.get("BENCH_TMP", tempfile.gettempdir()), "bench_out_%d.%s" % (os.getpid(), fmt))
    timed(lambda: A(x.tobytes(), frame_rate=rate, sample_width=2, channels=ch).export(out, format=fmt))
    R["check"] = {"bytes": os.path.getsize(out)}
    os.remove(out)


def op_dsp(lib, a):
    what, name = a
    s = seg_of(lib, name)
    fx = sub(lib, "effects")
    if what == "overlay":
        other = seg_of(lib, name.replace("speech", "music"))
        r = timed(lambda: s.overlay(other))
    elif what == "fade":
        r = timed(lambda: s.fade_in(3000).fade_out(3000))
    elif what == "gain":
        r = timed(lambda: s.apply_gain(6))
    elif what == "normalize":
        r = timed(lambda: fx.normalize(s))
    elif what == "speedup":
        r = timed(lambda: fx.speedup(s, 1.5))
    elif what == "lowpass":
        r = timed(lambda: fx.low_pass_filter(s, 3000))
    elif what == "compress":
        r = timed(lambda: fx.compress_dynamic_range(s))
    elif what == "dbfs":
        r = timed(lambda: [s[i:i + 5].dBFS for i in range(len(s) - 5)])
        R["check"] = {"n": len(r)}
        return
    elif what == "append":
        chunk = s[:100]

        def go():
            t = s[:100]
            for _ in range(1000):
                t += chunk
            return t
        r = timed(go)
    R["check"] = chk(r)


def op_silence(lib, a):
    what, name = a
    s = seg_of(lib, name)
    m = sub(lib, "silence")
    if what == "detect":
        r = timed(lambda: m.detect_silence(s, min_silence_len=1000, silence_thresh=-40))
        R["check"] = {"n": len(r), "sum": sum(b - a for a, b in r)}
    else:
        r = timed(lambda: m.split_on_silence(s, min_silence_len=1000, silence_thresh=-40))
        R["check"] = {"n": len(r), "sum": sum(len(c) for c in r)}


def op_vad(lib, a):
    import numpy as np
    raw, rate, ch = read_wav(a[0])
    if lib == "pydub2":
        s = seg_of(lib, a[0])
        r = timed(lambda: sub(lib, "silence").detect_speech(s))
        R["check"] = {"n": len(r), "sum": sum(b - a for a, b in r) / 1000}
    else:
        import torch
        from silero_vad import get_speech_timestamps, load_silero_vad
        model = load_silero_vad(onnx=True)
        wav = torch.from_numpy(np.frombuffer(raw, np.int16).astype(np.float32) / 32768)
        r = timed(lambda: get_speech_timestamps(wav, model, sampling_rate=16000))
        R["check"] = {"n": len(r), "sum": sum(d["end"] - d["start"] for d in r) / 16000}


OPS = {k[3:]: v for k, v in globals().items() if k.startswith("op_")}


def worker(lib, op, args):
    try:
        OPS[op](lib, args)
    except Unsupported:
        R["error"] = "unsupported"
    except Exception as e:
        R["error"] = ("%s: %s" % (type(e).__name__, e))[:300]
    print(json.dumps(R))


def cases(quick):
    skip = ["librosa", "audioread", "silero"] if quick else []
    c = [("none", [], ["pydub"]), ("import", [], list(IMPORT))]
    for kind, durs in (("speech", ("60s", "10m") if quick else ("5s", "60s", "10m")), ("music", ("60s", "10m") if quick else ("60s",))):
        for dur in durs:
            for lay in LAYOUTS:
                for ext in EXTS:
                    c.append(("load", ["%s_%s_%s.%s" % (kind, dur, lay, ext)], LOADERS))
    for dur in ("60s", "10m"):
        for lay in LAYOUTS:
            for ext in EXTS:
                c.append(("load10", ["speech_%s_%s.%s" % (dur, lay, ext)], LOADERS[:-1]))
    for f in ("speech_60s_s44", "speech_10m_s44") if quick else ("speech_60s_s44", "speech_5s_s44", "speech_10m_s44"):
        for fmt in EXTS:
            c.append(("export", [fmt, f + ".wav"], PYDUBS + ["sf", "av", "mini"] + (["pydub2mp3"] if fmt == "mp3" else [])))
    for f in ("speech_60s_s44", "speech_10m_s44"):
        for q in ("default",) if quick else ("default", "high"):
            c.append(("resample", [q, f + ".wav"], PYDUBS + ["av", "mini", "librosa"]))
    for f in ("speech_60s_s44", "speech_10m_s44"):
        for ext in ("wav", "mp3", "flac"):
            c.append(("gradio_in", [f + "." + ext], PYDUBS))
            c.append(("gradio_in_fast", [f + "." + ext], ["pydub2"]))
        for fmt in ("wav", "mp3"):
            c.append(("gradio_out", [fmt, f + ".wav"], PYDUBS + (["pydub2mp3"] if fmt == "mp3" else [])))
    for what in ("overlay", "normalize", "compress", "lowpass", "append") if quick else ("overlay", "fade", "gain", "normalize", "speedup", "compress", "lowpass", "dbfs", "append"):
        c.append(("dsp", [what, "speech_60s_s44.wav"], PYDUBS))
    for what in ("overlay", "normalize", "compress", "lowpass") if quick else ("overlay", "fade", "gain", "normalize"):
        c.append(("dsp", [what, "speech_10m_s44.wav"], PYDUBS))
    for what in ("detect", "split"):
        c.append(("silence", [what, "speech_10m_s44.wav"], PYDUBS))
    for f in ("speech_10m_m16",) if quick else ("speech_60s_m16", "speech_10m_m16"):
        c.append(("vad", [f + ".wav"], ["pydub2"] if quick else ["pydub2", "silero"]))
    order = ["none", "import", "dsp", "silence", "vad", "gradio_in", "gradio_in_fast", "gradio_out", "resample", "export", "load10", "load"]
    return sorted([(o, a, [l for l in ls if l not in skip]) for o, a, ls in c], key=lambda x: order.index(x[0]))


def py(venvs, lib):
    return os.path.join(venvs, VENV.get(lib, lib), "bin", "python")


def one(venvs, data, lib, op, args, timeout, path=None):
    env = dict(os.environ, BENCH_DATA=data)
    if path:
        env["PATH"] = path
    load = os.getloadavg()[0]
    try:
        p = subprocess.run([py(venvs, lib), os.path.abspath(__file__), "worker", lib, op] + args,
                           capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    lines = p.stdout.strip().splitlines()
    if p.returncode or not lines:
        return {"error": "crash rc=%s %s" % (p.returncode, p.stderr.strip()[-200:])}
    r = json.loads(lines[-1])
    r["load"] = load
    return r


def spread(v):
    return (max(v) - min(v)) / statistics.median(v)


def run(venvs, data, out, runs, only, skip, libs, slow, timeout, quick):
    done = set()
    if os.path.exists(out):
        done = {(j["case"], j["lib"]) for j in map(json.loads, open(out))}
    for op, args, ls in cases(quick):
        key = " ".join([op] + args)
        if (only and only not in key) or (skip and skip in key):
            continue
        ls = [l for l in ls if (not libs or l in libs) and (key, l) not in done]
        for a in args:
            if a.endswith(tuple("." + e for e in EXTS)):
                with open(os.path.join(data, a), "rb") as f:
                    while f.read(1 << 24):
                        pass
        res = {l: [] for l in ls}
        bad = {}
        want = {l: runs for l in ls}
        extra = 0
        while True:
            todo = [l for l in ls if l not in bad and len(res[l]) < want[l]]
            if not todo:
                noisy = [l for l in ls if l not in bad and want[l] >= runs and len(res[l]) >= runs and spread([r["wall"] for r in res[l]]) > 0.15]
                if not noisy or extra == 2:
                    break
                extra += 1
                for l in noisy:
                    want[l] += 5
                continue
            for l in todo:
                r = one(venvs, data, l, op, args, timeout)
                if "error" in r:
                    bad[l] = r
                    continue
                res[l].append(r)
                if len(res[l]) == 1 and r["wall"] > slow:
                    want[l] = 3
        for l in ls:
            rec = {"case": key, "lib": l}
            if l in bad:
                rec.update(status="n/a" if bad[l]["error"] == "unsupported" else "fail", err=bad[l]["error"])
            else:
                rec.update(status="ok", runs=res[l], check=res[l][-1].get("check"))
            with open(out, "a") as f:
                f.write(json.dumps(rec) + "\n")
            print(key, l, rec["status"], flush=True)


def med(rec, k):
    return statistics.median(r[k] for r in rec["runs"])


def cell(rec):
    if rec is None:
        return ""
    if rec["status"] != "ok":
        return rec["status"]
    v = [r["wall"] for r in rec["runs"]]
    flag = "*" if len(v) < 5 or spread(v) > 0.15 else ""
    return "%.3g/%.3gs %.0fM%s" % (med(rec, "wall"), med(rec, "cpu"), med(rec, "peak"), flag)


def report(out):
    recs = [json.loads(l) for l in open(out)]
    libs = [l for l in list(IMPORT) if any(r["lib"] == l for r in recs)]
    cs = list(dict.fromkeys(r["case"] for r in recs))
    by = {(r["case"], r["lib"]): r for r in recs}
    print("| case | " + " | ".join(libs) + " | load |")
    print("|---" * (len(libs) + 2) + "|")
    for c in cs:
        ld = [r["load"] for l in libs if (c, l) in by and by[(c, l)]["status"] == "ok" for r in by[(c, l)]["runs"]]
        print("| %s | %s | %.0f |" % (c, " | ".join(cell(by.get((c, l))) for l in libs), statistics.median(ld) if ld else 0))
    print("\nload is the median 1 min load average at the start of the runs of that row (10 cores)")
    print("cell is median wall / median cpu seconds, peak MiB (self + children), * = fewer than 5 runs or (max - min) / median over 15% after repeating")
    loads = [r["load"] for x in recs if x["status"] == "ok" for r in x["runs"]]
    print("1 min load average at start of the runs: min %.1f median %.1f max %.1f" % (min(loads), statistics.median(loads), max(loads)))
    print("\nchecks that differ between libraries for the same case:")
    for c in cs:
        ck = {l: by[(c, l)]["check"] for l in libs if (c, l) in by and by[(c, l)]["status"] == "ok" and by[(c, l)]["check"]}
        for k in ("n", "m", "sum"):
            v = {l: d[k] for l, d in ck.items() if k in d}
            if len(v) > 1 and (max(v.values()) - min(v.values())) > 0.03 * max(abs(max(v.values())), 1e-9):
                print(c, k, v)


def size_of(names):
    import importlib.metadata as md
    import re
    seen = {}

    def walk(n):
        n = re.sub(r"[-_.]+", "-", n).lower()
        if n in seen or n in ("pip", "setuptools"):
            return
        try:
            d = md.distribution(n)
        except md.PackageNotFoundError:
            return
        seen[n] = sum(f.locate().stat().st_size for f in d.files if f.locate().exists())
        for r in d.requires or []:
            if "extra ==" not in r:
                walk(re.split(r"[ ;<>=!\[(~]", r)[0])
    for n in names:
        walk(n)
    return seen


ROOTS = {"pydub": ["pydub", "audioop-lts"], "pydublts": ["pydub-lts"], "pydub2": ["pydub2"], "pydub2mp3": ["pydub2", "pydub2-mp3"],
         "sf": ["soundfile"], "av": ["av"], "mini": ["miniaudio"], "librosa": ["librosa", "audioread"], "silero": ["silero-vad", "onnxruntime"]}


def footprint(venvs, wheels):
    import glob
    for lib, names in ROOTS.items():
        v = VENV.get(lib, lib)
        if not os.path.isdir(os.path.join(venvs, v)):
            continue
        p = subprocess.run([py(venvs, v), os.path.abspath(__file__), "size"] + names, capture_output=True, text=True)
        sizes = json.loads(p.stdout)
        np_size = sizes.pop("numpy", 0)
        tmp = tempfile.mkdtemp()
        w = 0
        for n in names:
            local = glob.glob(os.path.join(wheels, n.replace("-", "_") + "-*.whl"))
            if not local:
                subprocess.run([py(venvs, v), "-m", "pip", "download", "-q", "--no-deps", "-d", tmp, n], capture_output=True)
            w += sum(os.path.getsize(f) for f in local or glob.glob(os.path.join(tmp, "*")) if n.replace("-", "_").lower() in os.path.basename(f).lower().replace("-", "_"))
        print("%-10s installed %7.1f MB without numpy (numpy %.1f MB) in %d dists, wheels of the named packages %.2f MB" % (lib, sum(sizes.values()) / 1e6, np_size / 1e6, len(sizes), w / 1e6))


def noffmpeg(venvs, data):
    path = "/usr/bin:/bin"
    print("| lib | " + " | ".join("read " + e for e in EXTS) + " | " + " | ".join("write " + e for e in EXTS) + " |")
    print("|---" * 11 + "|")
    for lib in ["pydub", "pydublts", "pydub2", "pydub2mp3", "sf", "av", "mini", "librosa", "audioread"]:
        if not os.path.isdir(os.path.join(venvs, VENV.get(lib, lib))):
            continue
        row = []
        for op, args in [("load", ["speech_5s_s44." + e]) for e in EXTS] + [("export", [e, "speech_5s_s44.wav"]) for e in EXTS]:
            r = one(venvs, data, lib, op, args, 120, path)
            row.append("no" if "error" in r and r["error"] != "unsupported" else "n/a" if "error" in r else "yes")
        print("| %s | %s |" % (lib, " | ".join(row)))


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "gen":
        gen(a[2])
    elif a[1] == "worker":
        worker(a[2], a[3], a[4:])
    elif a[1] == "size":
        print(json.dumps(size_of(a[2:])))
    elif a[1] == "run":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--venvs", required=True)
        ap.add_argument("--data", required=True)
        ap.add_argument("--out", required=True)
        ap.add_argument("--runs", type=int, default=5)
        ap.add_argument("--only", default="")
        ap.add_argument("--skip", default="")
        ap.add_argument("--libs", default="")
        ap.add_argument("--slow", type=float, default=60)
        ap.add_argument("--timeout", type=float, default=1800)
        ap.add_argument("--quick", action="store_true")
        o = ap.parse_args(a[2:])
        run(o.venvs, o.data, o.out, o.runs, o.only, o.skip, [l for l in o.libs.split(",") if l], o.slow, o.timeout, o.quick)
    elif a[1] == "report":
        report(a[2])
    elif a[1] == "footprint":
        footprint(a[2], a[3])
    elif a[1] == "noffmpeg":
        noffmpeg(a[2], a[3])
