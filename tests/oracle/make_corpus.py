import array
import math
import os
import shutil
import struct
import subprocess
import sys
import wave

out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "corpus")
tmp = os.path.join(out, ".src")
missing = [t for t in ("ffmpeg", "lame", "flac", "metaflac", "oggenc", "opusenc", "sox") if not shutil.which(t)]


def run(*args):
    p = subprocess.run(list(args), capture_output=True)
    return p.returncode == 0


def path(sub, name):
    d = os.path.join(out, sub)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def source(rate, ch, n):
    # 32 bit int so 24 and 32 bit targets get real low bits
    name = os.path.join(tmp, "src_%d_%d_%d.wav" % (rate, ch, n))
    if os.path.exists(name):
        return name
    os.makedirs(tmp, exist_ok=True)
    seed = 12345
    buf = array.array("i")
    for i in range(n):
        t = i / rate
        for c in range(ch):
            seed = (seed * 1103515245 + 12345) & 0x7fffffff
            noise = seed / 0x7fffffff - 0.5
            v = 0.45 * math.sin(2 * math.pi * (220 + 110 * c) * t) + 0.2 * math.sin(2 * math.pi * 3150 * t) + 0.05 * noise
            buf.append(int(v * 2147483647))
    w = wave.open(name, "wb")
    w.setnchannels(ch)
    w.setsampwidth(4)
    w.setframerate(rate)
    w.writeframes(buf.tobytes())
    w.close()
    return name


def ff(dst, rate, ch, n, *opts, extra_in=()):
    return run("ffmpeg", "-y", "-v", "error", "-i", source(rate, ch, n), *extra_in, *opts, dst)


SEC = {"0": lambda r: 0, "1smp": lambda r: 1, "10ms": lambda r: r // 100, "1s": lambda r: r, "61s": lambda r: r * 61}
RATES = [8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000]


def make_wav():
    codecs = ["pcm_u8", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le", "pcm_f64le",
              "pcm_alaw", "pcm_mulaw", "adpcm_ms", "adpcm_ima_wav"]
    for c in codecs:
        for ch in (1, 2, 6):
            ff(path("wav", "%s_44100_%dch_1s.wav" % (c, ch)), 44100, ch, 44100, "-c:a", c)
    for r in RATES:
        ff(path("wav", "pcm_s16le_%d_2ch_1s.wav" % r), r, 2, r, "-c:a", "pcm_s16le")
        ff(path("wav", "pcm_s24le_%d_1ch_1s.wav" % r), r, 1, r, "-c:a", "pcm_s24le")
    for d, f in SEC.items():
        for c, ch in (("pcm_s16le", 2), ("pcm_s24le", 2), ("pcm_f32le", 1), ("pcm_u8", 1)):
            ff(path("wav", "%s_44100_%dch_%s.wav" % (c, ch, d)), 44100, ch, f(44100), "-c:a", c)
    ff(path("wav", "pcm_s16le_8ch_1s.wav"), 48000, 8, 48000, "-c:a", "pcm_s16le")
    ff(path("wav", "rf64_always.wav"), 44100, 2, 44100, "-c:a", "pcm_s16le", "-rf64", "always")
    ff(path("wav", "bext.wav"), 44100, 2, 44100, "-c:a", "pcm_s16le", "-write_bext", "1")
    ff(path("wav", "peak.wav"), 44100, 2, 44100, "-c:a", "pcm_s16le", "-write_peak", "on")
    ff(path("wav", "tags.wav"), 44100, 2, 44100, "-c:a", "pcm_s16le", "-metadata", "title=hello", "-metadata", "artist=me")
    ff(path("wav", "mp3_in_wav.wav"), 44100, 2, 44100, "-c:a", "libmp3lame", "-b:a", "128k")
    ff(path("wav", "gsm_in_wav.wav"), 8000, 1, 8000, "-c:a", "gsm_ms")
    ff(path("wav", "g726_in_wav.wav"), 8000, 1, 8000, "-c:a", "adpcm_g726", "-b:a", "32k")
    mangle_wav()


def chunk(cid, body):
    return cid + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")


def fmt(tag, ch, rate, bits, block=None, ext=None):
    block = block or ch * bits // 8
    body = struct.pack("<HHIIHH", tag, ch, rate, rate * block, block, bits)
    if ext is not None:
        valid, sub = ext
        body += struct.pack("<HHI", 22, valid, 3 if ch == 2 else 4) + struct.pack("<H", sub) + b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
    return chunk(b"fmt ", body)


def riff(*chunks, size=None):
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body) if size is None else size) + body


def mangle_wav():
    w = wave.open(source(44100, 2, 44100))
    pcm32 = w.readframes(44100)
    pcm = b"".join(pcm32[i + 2:i + 4] for i in range(0, len(pcm32), 4))
    f16 = fmt(1, 2, 44100, 16)
    data = chunk(b"data", pcm)
    info = chunk(b"LIST", b"INFO" + chunk(b"INAM", b"name\0") + chunk(b"IART", b"someone\0"))
    cue = chunk(b"cue ", struct.pack("<I", 1) + struct.pack("<II4sIII", 1, 0, b"data", 0, 0, 1000))
    files = {
        "data_size_0": riff(f16, b"data" + struct.pack("<I", 0) + pcm),
        "data_size_ffffffff": riff(f16, b"data" + struct.pack("<I", 0xffffffff) + pcm),
        "data_size_too_big": riff(f16, b"data" + struct.pack("<I", len(pcm) * 3) + pcm),
        "data_size_too_small": riff(f16, b"data" + struct.pack("<I", len(pcm) // 2) + pcm),
        "riff_size_0": riff(f16, data, size=0),
        "riff_size_ffffffff": riff(f16, data, size=0xffffffff),
        "odd_byte_after_data": riff(f16, b"data" + struct.pack("<I", len(pcm) + 1) + pcm + b"\x01"),
        "truncated_mid_frame": riff(f16, data)[:-3],
        "list_before_data": riff(f16, info, data),
        "cue_list_after_data": riff(f16, data, cue, info),
        "junk_before_fmt": riff(chunk(b"JUNK", b"\0" * 28), f16, data),
        "fact_chunk": riff(f16, chunk(b"fact", struct.pack("<I", 44100)), data),
        "fmt_size_18": riff(chunk(b"fmt ", f16[8:] + b"\0\0"), data),
        "id3_chunk_after_data": riff(f16, data, chunk(b"id3 ", b"ID3\x03\0\0\0\0\0\x0a" + b"\0" * 10)),
        "trailing_garbage": riff(f16, data) + b"garbage!" * 100,
        "header_only": riff(f16, chunk(b"data", b"")),
        "header_only_claims_size": riff(f16, b"data" + struct.pack("<I", 1000)),
        "no_data_chunk": riff(f16),
        "ext_s16": riff(fmt(0xfffe, 2, 44100, 16, ext=(16, 1)), data),
        "ext_float": riff(fmt(0xfffe, 2, 44100, 32, ext=(32, 3)), chunk(b"data", array.array("f", [s / 32768 for s in array.array("h", pcm)]).tobytes())),
        "ext_24_in_32": riff(fmt(0xfffe, 2, 44100, 32, ext=(24, 1)), chunk(b"data", bytes(b & 0xff if i % 4 else 0 for i, b in enumerate(pcm32)))),
        "ext_20_in_24": riff(fmt(0xfffe, 2, 44100, 24, ext=(20, 1)), chunk(b"data", b"".join(pcm32[i + 1:i + 4] for i in range(0, len(pcm32), 4)))),
        "wrong_byterate": riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 12345, 4, 16)), data),
        "wrong_blockalign": riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 2, 16)), data),
        "two_data_chunks": riff(f16, chunk(b"data", pcm[:4000]), chunk(b"data", pcm[4000:8000])),
        "bits_12_in_16": riff(chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 12)), data),
        "rifx_like_lowercase_fmt": riff(chunk(b"FMT ", f16[8:]), f16, data),
    }
    for k, v in files.items():
        with open(path("wav_mangled", k + ".wav"), "wb") as f:
            f.write(v)


def make_flac():
    for r in RATES:
        ff(path("flac", "s16_%d_2ch_1s.flac" % r), r, 2, r, "-c:a", "flac", "-sample_fmt", "s16")
    for r in (44100, 96000, 192000):
        for ch in (1, 2, 6, 8):
            ff(path("flac", "s24_%d_%dch_1s.flac" % (r, ch)), r, ch, r, "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24")
    for d, f in SEC.items():
        ff(path("flac", "s16_44100_2ch_%s.flac" % d), 44100, 2, f(44100), "-c:a", "flac", "-sample_fmt", "s16")
    ff(path("flac", "tags_cover.flac"), 44100, 2, 44100, "-map", "0", "-map", "1", "-c:a", "flac", "-c:v", "png", "-disposition:v", "attached_pic",
       "-metadata", "title=hello", extra_in=("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1"))
    if "flac" in missing:
        return
    os.makedirs(tmp, exist_ok=True)
    for bits in (8, 16, 24, 32):
        for r in (8000, 11111, 37800, 44100):
            src = os.path.join(tmp, "b%d_%d.wav" % (bits, r))
            run("ffmpeg", "-y", "-v", "error", "-i", source(r, 2, r), "-c:a", {8: "pcm_u8", 16: "pcm_s16le", 24: "pcm_s24le", 32: "pcm_s32le"}[bits], src)
            run("flac", "-s", "-f", "-o", path("flac", "cli_%dbit_%d.flac" % (bits, r)), src)
    src = os.path.join(tmp, "b16_44100.wav")
    run("flac", "-s", "-f", "--no-seektable", "--no-padding", "-o", path("flac", "cli_no_seektable.flac"), src)
    run("flac", "-s", "-f", "-l", "0", "-b", "4608", "-o", path("flac", "cli_verbatim.flac"), src)
    run("flac", "-s", "-f", "-8", "-o", path("flac", "cli_best.flac"), src)
    run("flac", "-s", "-f", "-0", "-T", "TITLE=x", "-T", "ARTIST=y", "-o", path("flac", "cli_fast_tags.flac"), src)
    run("flac", "-s", "-f", "--ogg", "-o", path("ogg_flac", "cli_ogg.oga"), src)
    shutil.copy(path("ogg_flac", "cli_ogg.oga"), path("ogg_flac", "cli_ogg.ogg"))
    pic = os.path.join(tmp, "big.png")
    run("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=1200x1200,geq=random(1)*255:128:128", "-frames:v", "1", pic)
    run("flac", "-s", "-f", "--picture=" + pic, "-o", path("flac", "cli_big_cover.flac"), src)
    big = path("flac", "id3_before_flac.flac")
    shutil.copy(path("flac", "cli_best.flac"), big)
    data = open(big, "rb").read()
    open(big, "wb").write(b"ID3\x03\0\0\0\0\x01\x00" + b"\0" * 128 + data)


def make_mp3():
    os.makedirs(tmp, exist_ok=True)
    for d, f in SEC.items():
        ff(path("mp3", "ffmpeg_cbr128_%s.mp3" % d), 44100, 2, f(44100), "-c:a", "libmp3lame", "-b:a", "128k")
        ff(path("mp3", "ffmpeg_v2_%s.mp3" % d), 44100, 2, f(44100), "-c:a", "libmp3lame", "-q:a", "2")
    for r in (8000, 11025, 12000, 16000, 22050, 24000, 32000, 48000):
        ff(path("mp3", "ffmpeg_%d_mono.mp3" % r), r, 1, r, "-c:a", "libmp3lame", "-b:a", "32k")
    ff(path("mp3", "ffmpeg_no_xing.mp3"), 44100, 2, 44100, "-c:a", "libmp3lame", "-b:a", "128k", "-write_xing", "0")
    ff(path("mp3", "ffmpeg_id3v23_tags.mp3"), 44100, 2, 44100, "-c:a", "libmp3lame", "-id3v2_version", "3", "-metadata", "title=t", "-metadata", "artist=a")
    ff(path("mp3", "ffmpeg_id3v1.mp3"), 44100, 2, 44100, "-c:a", "libmp3lame", "-write_id3v1", "1", "-metadata", "title=t")
    ff(path("mp3", "ffmpeg_cover.mp3"), 44100, 2, 44100, "-map", "0", "-map", "1", "-c:a", "libmp3lame", "-c:v", "mjpeg", "-disposition:v", "attached_pic",
       extra_in=("-f", "lavfi", "-i", "nullsrc=s=800x800,geq=random(1)*255:128:128", "-frames:v", "1"))
    ff(path("mp3", "mp2_named_mp3.mp3"), 44100, 2, 44100, "-c:a", "mp2", "-f", "mp2")
    ff(path("mp3", "6ch_downmixed.mp3"), 44100, 6, 44100, "-c:a", "libmp3lame", "-ac", "2")
    if "lame" not in missing:
        src = os.path.join(tmp, "s16_44100.wav")
        run("ffmpeg", "-y", "-v", "error", "-i", source(44100, 2, 44100 * 3), "-c:a", "pcm_s16le", src)
        opts = {
            "cbr32": ["-b", "32"], "cbr64": ["-b", "64"], "cbr128": ["-b", "128"], "cbr192": ["-b", "192"], "cbr320": ["-b", "320"],
            "v0": ["-V", "0"], "v2": ["-V", "2"], "v5": ["-V", "5"], "v9": ["-V", "9"], "abr96": ["--abr", "96"],
            "mono": ["-m", "m", "-b", "64"], "joint": ["-m", "j"], "stereo": ["-m", "s"], "dual": ["-m", "d"], "forced_ms": ["-m", "f"],
            "no_tag": ["-t"], "nores": ["--nores"], "strictiso": ["--strictly-enforce-ISO"],
            "mpeg2_22k": ["--resample", "22.05"], "mpeg2_16k": ["--resample", "16"], "mpeg2_24k": ["--resample", "24"],
            "mpeg25_8k": ["--resample", "8"], "mpeg25_11k": ["--resample", "11.025"], "mpeg25_12k": ["--resample", "12"],
            "mpeg1_32k": ["--resample", "32"], "mpeg1_48k": ["--resample", "48"],
            "freeformat": ["--freeformat", "-b", "400"],
            "id3v1_only": ["--id3v1-only", "--tt", "title", "--ta", "artist"],
            "id3v2_only": ["--id3v2-only", "--tt", "title", "--ta", "artist"],
            "id3v2_pad": ["--id3v2-only", "--pad-id3v2-size", "5000", "--tt", "x"],
        }
        for k, o in opts.items():
            run("lame", "--quiet", *o, src, path("mp3", "lame_%s.mp3" % k))
        pic = os.path.join(tmp, "big.jpg")
        run("ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "nullsrc=s=1400x1400,geq=random(1)*255:128:128", "-frames:v", "1", "-q:v", "1", pic)
        run("lame", "--quiet", "-V", "2", "--ti", pic, src, path("mp3", "lame_big_cover.mp3"))
        for d, f in SEC.items():
            s = os.path.join(tmp, "s16_%s.wav" % d)
            run("ffmpeg", "-y", "-v", "error", "-i", source(44100, 2, f(44100)), "-c:a", "pcm_s16le", s)
            run("lame", "--quiet", "-V", "2", s, path("mp3", "lame_v2_%s.mp3" % d))
        base = open(path("mp3", "lame_v2.mp3"), "rb").read()
        cbr = open(path("mp3", "lame_cbr128.mp3"), "rb").read()
        item = struct.pack("<II", 4, 0) + b"Title\0" + b"abcd"
        ape_footer = b"APETAGEX" + struct.pack("<IIII", 2000, 32 + len(item), 1, 0x80000000) + b"\0" * 8
        id3v1 = b"TAG" + b"title".ljust(30, b"\0") + b"\0" * 94
        mangled = {
            "garbage_before_sync": b"\x00\x11garbage" * 100 + base,
            "zeros_before_sync": b"\0" * 4096 + base,
            "ape_tag": base + item + ape_footer,
            "ape_and_id3v1": base + item + ape_footer + id3v1,
            "id3v1_appended": base + id3v1,
            "two_id3v2": b"ID3\x03\0\0\0\0\0\x14" + b"TIT2\0\0\0\x0a\0\0\0first\0\0\0\0" + b"ID3\x03\0\0\0\0\0\x14" + b"TIT2\0\0\0\x0a\0\0\0secnd\0\0\0\0" + base,
            "truncated_last_frame": base[:-200],
            "cbr_truncated": cbr[:len(cbr) // 2 + 7],
            "cbr_cut_start": cbr[4000:],
            "riff_wrapped": riff(chunk(b"fmt ", struct.pack("<HHIIHH", 0x55, 2, 44100, 16000, 1, 0) + b"\x0c\0\x01\0\x02\0\0\0\x40\x03\x01\0\x71\x05"), chunk(b"data", cbr)),
        }
        for k, v in mangled.items():
            with open(path("mp3", "mangled_%s.mp3" % k), "wb") as f:
                f.write(v)
        os.rename(path("mp3", "mangled_riff_wrapped.mp3"), path("mp3", "mangled_riff_wrapped.wav"))


def make_ogg():
    for q in (-1, 0, 3, 6, 10):
        ff(path("vorbis", "q%d_44100_2ch.ogg" % q), 44100, 2, 44100, "-c:a", "libvorbis", "-q:a", str(q))
    for r in (8000, 11025, 16000, 22050, 32000, 44100, 48000, 96000, 192000):
        ff(path("vorbis", "q3_%d_1ch.ogg" % r), r, 1, r, "-c:a", "libvorbis", "-q:a", "3")
    for ch in (1, 2, 6):
        ff(path("vorbis", "q3_48000_%dch.ogg" % ch), 48000, ch, 48000, "-c:a", "libvorbis")
    for d, f in SEC.items():
        ff(path("vorbis", "q3_44100_2ch_%s.ogg" % d), 44100, 2, f(44100), "-c:a", "libvorbis")
    ff(path("vorbis", "tags.ogg"), 44100, 2, 44100, "-c:a", "libvorbis", "-metadata", "title=x", "-metadata", "artist=y")
    ff(path("vorbis", "oga_ext.oga"), 44100, 2, 44100, "-c:a", "libvorbis")
    ff(path("vorbis", "vorbis_in_webm.webm"), 44100, 2, 44100, "-c:a", "libvorbis")
    ff(path("vorbis", "vorbis_in_mka.mka"), 44100, 2, 44100, "-c:a", "libvorbis")
    cat("vorbis", "chained_same.ogg", "vorbis/q3_44100_2ch_1s.ogg", "vorbis/q3_44100_2ch_1s.ogg")
    cat("vorbis", "chained_rate_change.ogg", "vorbis/q3_44100_2ch_1s.ogg", "vorbis/q3_22050_1ch.ogg")
    cat("vorbis", "chained_ch_change.ogg", "vorbis/q3_48000_2ch.ogg", "vorbis/q3_48000_6ch.ogg")
    cat("vorbis", "trailing_garbage.ogg", "vorbis/q3_44100_2ch_1s.ogg", None, tail=b"\x00junk" * 200)
    trunc("vorbis", "truncated.ogg", "vorbis/q3_44100_2ch_1s.ogg")

    for b in ("6k", "16k", "32k", "64k", "128k", "256k"):
        ff(path("opus", "b%s_48000_2ch.opus" % b), 48000, 2, 48000, "-c:a", "libopus", "-b:a", b)
    for app in ("voip", "audio", "lowdelay"):
        ff(path("opus", "app_%s.opus" % app), 48000, 1, 48000, "-c:a", "libopus", "-application", app)
    for r in (8000, 12000, 16000, 24000, 44100, 48000):
        ff(path("opus", "in_%d_1ch.opus" % r), r, 1, r, "-c:a", "libopus")
    for ch in (1, 2, 6, 8):
        ff(path("opus", "48000_%dch.opus" % ch), 48000, ch, 48000, "-c:a", "libopus")
    for d, f in SEC.items():
        ff(path("opus", "48000_2ch_%s.opus" % d), 48000, 2, f(48000), "-c:a", "libopus")
    for fd in ("2.5", "20", "60"):
        ff(path("opus", "frame_%s.opus" % fd), 48000, 2, 48000, "-c:a", "libopus", "-frame_duration", fd)
    ff(path("opus", "cbr.opus"), 48000, 2, 48000, "-c:a", "libopus", "-vbr", "off")
    ff(path("opus", "opus_named_ogg.ogg"), 48000, 2, 48000, "-c:a", "libopus")
    ff(path("opus", "opus_in_webm.webm"), 48000, 2, 48000, "-c:a", "libopus")
    ff(path("opus", "tags.opus"), 48000, 2, 48000, "-c:a", "libopus", "-metadata", "title=x")
    cat("opus", "chained_same.opus", "opus/48000_2ch_1s.opus", "opus/48000_2ch_1s.opus")
    cat("opus", "chained_ch_change.opus", "opus/48000_2ch_1s.opus", "opus/48000_1ch.opus")
    trunc("opus", "truncated.opus", "opus/48000_2ch_1s.opus")


def cat(sub, name, a, b, tail=b""):
    data = open(os.path.join(out, a), "rb").read() + (open(os.path.join(out, b), "rb").read() if b else b"") + tail
    open(path(sub, name), "wb").write(data)


def trunc(sub, name, a):
    data = open(os.path.join(out, a), "rb").read()
    open(path(sub, name), "wb").write(data[:len(data) * 2 // 3])


def make_other():
    ff(path("other", "aac.m4a"), 44100, 2, 44100, "-c:a", "aac")
    ff(path("other", "aac.aac"), 44100, 2, 44100, "-c:a", "aac", "-f", "adts")
    ff(path("other", "alac.m4a"), 44100, 2, 44100, "-c:a", "alac")
    ff(path("other", "s16.aiff"), 44100, 2, 44100, "-c:a", "pcm_s16be")
    ff(path("other", "s24.aif"), 44100, 2, 44100, "-c:a", "pcm_s24be")
    ff(path("other", "wavpack.wv"), 44100, 2, 44100, "-c:a", "wavpack")
    ff(path("other", "ac3.ac3"), 48000, 6, 48000, "-c:a", "ac3")
    ff(path("other", "w64.w64"), 44100, 2, 44100, "-c:a", "pcm_s16le")
    ff(path("other", "caf.caf"), 44100, 2, 44100, "-c:a", "pcm_s16le")
    ff(path("other", "au.au"), 44100, 2, 44100, "-c:a", "pcm_s16be")
    ff(path("other", "flac_in_mka.mka"), 44100, 2, 44100, "-c:a", "flac")
    ff(path("other", "mp3_in_mp4.mp4"), 44100, 2, 44100, "-c:a", "libmp3lame")


def make_wrong_ext():
    pairs = [("wav/pcm_s16le_44100_2ch_1s.wav", "wav_as.mp3"), ("mp3/ffmpeg_cbr128_1s.mp3", "mp3_as.wav"),
             ("mp3/ffmpeg_v2_1s.mp3", "mp3_as.ogg"), ("flac/s16_44100_2ch_1s.flac", "flac_as.wav"),
             ("flac/s16_44100_2ch_1s.flac", "flac_as.ogg"), ("vorbis/q3_44100_2ch_1s.ogg", "ogg_as.mp3"),
             ("vorbis/q3_44100_2ch_1s.ogg", "ogg_as.opus"), ("opus/48000_2ch_1s.opus", "opus_as.mp3"),
             ("other/aac.m4a", "m4a_as.mp3"), ("wav/pcm_s16le_44100_2ch_1s.wav", "wav_as.flac"),
             ("wav/pcm_s16le_44100_2ch_1s.wav", "wav_no_ext"), ("mp3/ffmpeg_cbr128_1s.mp3", "mp3_no_ext"),
             ("wav/pcm_s24le_44100_2ch_1s.wav", "WAV_UPPER.WAV"), ("mp3/ffmpeg_cbr128_1s.mp3", "MP3_UPPER.MP3")]
    for a, b in pairs:
        if os.path.exists(os.path.join(out, a)):
            shutil.copy(os.path.join(out, a), path("wrong_ext", b))
    for e in ("wav", "mp3", "flac", "ogg", "opus", "m4a"):
        open(path("empty", "zero_bytes." + e), "wb").close()
        open(path("empty", "one_byte." + e), "wb").write(b"\x01")
        open(path("empty", "text." + e), "wb").write(b"this is not audio\n" * 20)
    open(path("empty", "riff_only.wav"), "wb").write(b"RIFF\0\0\0\0WAVE")
    open(path("empty", "id3_only.mp3"), "wb").write(b"ID3\x03\0\0\0\0\0\x14" + b"TIT2\0\0\0\x0a\0\0\0title\0\0\0\0")
    open(path("empty", "flac_magic_only.flac"), "wb").write(b"fLaC")
    open(path("empty", "oggs_only.ogg"), "wb").write(b"OggS" + b"\0" * 23)


make_wav()
make_flac()
make_mp3()
make_ogg()
make_other()
make_wrong_ext()
shutil.rmtree(tmp, ignore_errors=True)
print("missing tools:", " ".join(missing) or "none")
print(sum(len(f) for _, _, f in os.walk(out)), "files in", out)
