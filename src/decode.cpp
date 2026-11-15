#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>
#include <memory>
#include <stdexcept>

#define DR_WAV_IMPLEMENTATION
#define DR_FLAC_IMPLEMENTATION
#define DR_MP3_IMPLEMENTATION
#include "third_party/dr_wav.h"
#include "third_party/dr_flac.h"
#include "third_party/dr_mp3.h"
#define STB_VORBIS_HEADER_ONLY
#include "third_party/stb_vorbis.c"
#include <opusfile.h>

#include "core.h"

namespace pydub2 {

static void fail() {
    throw std::runtime_error("cant read this file");
}

// same rounding and clipping as ffmpegs float to int
static void put(std::string &out, const float *f, size_t n, int width) {
    for (size_t i = 0; i < n; i++) {
        if (width == 2) {
            long v = lrintf(f[i] * 32768.0f);
            int16_t s = v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v;
            out.append((const char *)&s, 2);
        } else {
            long long v = llrintf(f[i] * 2147483648.0f);
            int32_t s = v > INT32_MAX ? INT32_MAX : v < INT32_MIN ? INT32_MIN : (int32_t)v;
            out.append((const char *)&s, 4);
        }
    }
}

static Audio read_wav(const uint8_t *buf, size_t len) {
    drwav wav;
    if (!drwav_init_memory(&wav, buf, len, NULL)) {
        fail();
    }
    std::unique_ptr<drwav, drwav_result (*)(drwav *)> done(&wav, drwav_uninit);
    Audio a;
    a.rate = wav.sampleRate;
    a.channels = wav.channels;
    int bits = wav.bitsPerSample;
    int tag = wav.translatedFormatTag;
    size_t n = wav.totalPCMFrameCount;
    size_t frame = wav.fmt.blockAlign;
    if (tag == DR_WAVE_FORMAT_PCM && (bits == 8 || bits == 16 || bits == 24 || bits == 32) && frame == (size_t)a.channels * bits / 8) {
        // the header can claim any size, the file cant hold more than len
        size_t bytes = std::min(n * frame, len);
        a.data.resize(bytes);
        a.data.resize(drwav_read_raw(&wav, bytes, &a.data[0]));
        if (bits == 8) {
            for (char &c : a.data) {
                c ^= 0x80;
            }
        }
        if (bits == 24) {
            // pydub pads the low byte with 00 or ff, not zeros
            std::string s;
            for (size_t i = 0; i + 2 < a.data.size(); i += 3) {
                s += (int8_t)a.data[i + 2] < 0 ? '\xff' : '\0';
                s.append(a.data, i, 3);
            }
            a.data = s;
        }
        a.width = bits == 24 ? 4 : bits / 8;
    } else if (tag == DR_WAVE_FORMAT_IEEE_FLOAT) {
        float tmp[4096];
        size_t k;
        a.width = 4;
        while ((k = drwav_read_pcm_frames_f32(&wav, 4096 / a.channels, tmp)) > 0) {
            put(a.data, tmp, k * a.channels, 4);
        }
    } else {
        fail();
    }
    return a;
}

static Audio read_flac(const uint8_t *buf, size_t len) {
    std::unique_ptr<drflac, void (*)(drflac *)> done(drflac_open_memory(buf, len, NULL), drflac_close);
    drflac *f = done.get();
    if (!f) {
        fail();
    }
    Audio a;
    a.rate = f->sampleRate;
    a.channels = f->channels;
    a.width = f->bitsPerSample <= 16 ? 2 : 4;
    int16_t s16[4096];
    int32_t s32[4096];
    size_t k;
    if (a.width == 2) {
        while ((k = drflac_read_pcm_frames_s16(f, 4096 / a.channels, s16)) > 0) {
            a.data.append((const char *)s16, k * a.channels * 2);
        }
    } else {
        while ((k = drflac_read_pcm_frames_s32(f, 4096 / a.channels, s32)) > 0) {
            a.data.append((const char *)s32, k * a.channels * 4);
        }
    }
    return a;
}

// dr_mp3 gives up on a short Info frame, so find it here. ffmpeg drops the frame and the lame delay and padding
static size_t mp3_tag(const uint8_t *buf, size_t len, size_t &skip, size_t &cut) {
    size_t at = 0;
    if (len >= 10 && !memcmp(buf, "ID3", 3)) {
        at = 10 + ((buf[6] & 127) << 21 | (buf[7] & 127) << 14 | (buf[8] & 127) << 7 | (buf[9] & 127)) + (buf[5] & 16 ? 10 : 0);
    }
    if (at + 4 > len || buf[at] != 0xff) {
        return 0;
    }
    drmp3dec dec;
    drmp3dec_init(&dec);
    drmp3dec_frame_info fi;
    const uint8_t *f = buf + at;
    if (!drmp3dec_decode_frame(&dec, f, (int)std::min(len - at, (size_t)INT_MAX), NULL, &fi) || fi.layer != 3) {
        return 0;
    }
    int side = (f[1] & 8) ? (fi.channels == 1 ? 17 : 32) : (fi.channels == 1 ? 9 : 17);
    int o = 4 + (f[1] & 1 ? 0 : 2) + side;
    if (o + 8 > fi.frame_bytes || (memcmp(f + o, "Xing", 4) && memcmp(f + o, "Info", 4))) {
        return 0;
    }
    int flags = f[o + 7];
    o += 8;
    const int sizes[] = {4, 4, 100, 4};
    for (int i = 0; i < 4; i++) {
        if (flags >> i & 1) {
            o += sizes[i];
        }
    }
    if (o + 24 <= fi.frame_bytes && (!memcmp(f + o, "LAME", 4) || !memcmp(f + o, "Lavf", 4) || !memcmp(f + o, "Lavc", 4))) {
        skip = (f[o + 21] << 4 | f[o + 22] >> 4) + 528 + 1;
        int pad = (f[o + 22] & 15) << 8 | f[o + 23];
        cut = pad > 529 ? pad - 529 : 0;
    }
    return at + fi.frame_bytes;
}

static Audio read_mp3(const uint8_t *buf, size_t len) {
    size_t skip = 0, cut = 0;
    size_t at = mp3_tag(buf, len, skip, cut);
    buf += at;
    len -= at;
    drmp3 mp3;
    if (!drmp3_init_memory(&mp3, buf, len, NULL)) {
        fail();
    }
    std::unique_ptr<drmp3, void (*)(drmp3 *)> done(&mp3, drmp3_uninit);
    Audio a;
    a.rate = mp3.sampleRate;
    a.channels = mp3.channels;
    a.width = 2;
    float tmp[4096];
    size_t k;
    while ((k = drmp3_read_pcm_frames_f32(&mp3, 4096 / a.channels, tmp)) > 0) {
        put(a.data, tmp, k * a.channels, 2);
    }
    size_t b = a.channels * 2;
    a.data.erase(0, std::min(a.data.size(), skip * b));
    a.data.resize(a.data.size() - std::min(a.data.size(), cut * b));
    return a;
}

static Audio read_vorbis(const uint8_t *buf, size_t len) {
    if (len > INT_MAX) {
        fail();
    }
    std::unique_ptr<stb_vorbis, void (*)(stb_vorbis *)> done(stb_vorbis_open_memory(buf, (int)len, NULL, NULL), stb_vorbis_close);
    stb_vorbis *v = done.get();
    if (!v) {
        fail();
    }
    stb_vorbis_info info = stb_vorbis_get_info(v);
    Audio a;
    a.rate = info.sample_rate;
    a.channels = info.channels;
    a.width = 4;
    float tmp[4096];
    int k;
    while ((k = stb_vorbis_get_samples_float_interleaved(v, a.channels, tmp, 4096 / a.channels * a.channels)) > 0) {
        put(a.data, tmp, k * a.channels, 4);
    }
    return a;
}

static Audio read_opus(const uint8_t *buf, size_t len) {
    std::unique_ptr<OggOpusFile, void (*)(OggOpusFile *)> done(op_open_memory(buf, len, NULL), op_free);
    OggOpusFile *of = done.get();
    if (!of) {
        fail();
    }
    Audio a;
    a.rate = 48000;
    a.channels = op_channel_count(of, -1);
    a.width = 4;
    float tmp[5760 * 2];
    int k, link;
    while ((k = op_read_float(of, tmp, 5760 * 2, &link)) != 0) {
        // a chained file can change channel count, k is sized for the new one
        if (k < 0 || op_channel_count(of, link) != a.channels) {
            fail();
        }
        put(a.data, tmp, k * a.channels, 4);
    }
    return a;
}

// the first packet sits after the page header and its segment table
static bool ogg_has(const uint8_t *buf, size_t len, const char *magic, size_t n) {
    return len >= 27 && len >= 27 + buf[26] + n && !memcmp(buf, "OggS", 4) && !memcmp(buf + 27 + buf[26], magic, n);
}

Audio decode(const uint8_t *buf, size_t len) {
    Audio a;
    if (len >= 12 && !memcmp(buf, "RIFF", 4) && !memcmp(buf + 8, "WAVE", 4)) {
        a = read_wav(buf, len);
    } else if (len >= 4 && !memcmp(buf, "fLaC", 4)) {
        a = read_flac(buf, len);
    } else if (ogg_has(buf, len, "OpusHead", 8)) {
        a = read_opus(buf, len);
    } else if (ogg_has(buf, len, "\x01vorbis", 7)) {
        a = read_vorbis(buf, len);
    } else if (len >= 4 && (!memcmp(buf, "ID3", 3) || (buf[0] == 0xff && (buf[1] & 0xe0) == 0xe0))) {
        a = read_mp3(buf, len);
    } else {
        fail();
    }
    if ((a.data.empty() && memcmp(buf, "RIFF", 4)) || a.channels < 1 || a.rate < 1) {
        fail();
    }
    return a;
}

}
