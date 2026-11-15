#include <cmath>
#include <cstring>
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
    Audio a;
    a.rate = wav.sampleRate;
    a.channels = wav.channels;
    int bits = wav.bitsPerSample;
    int tag = wav.translatedFormatTag;
    size_t n = wav.totalPCMFrameCount;
    size_t frame = wav.fmt.blockAlign;
    if (tag == DR_WAVE_FORMAT_PCM && (bits == 8 || bits == 16 || bits == 24 || bits == 32) && frame == (size_t)a.channels * bits / 8) {
        a.data.resize(n * frame);
        a.data.resize(drwav_read_raw(&wav, n * frame, &a.data[0]));
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
        drwav_uninit(&wav);
        fail();
    }
    drwav_uninit(&wav);
    return a;
}

static Audio read_flac(const uint8_t *buf, size_t len) {
    drflac *f = drflac_open_memory(buf, len, NULL);
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
    drflac_close(f);
    return a;
}

static Audio read_mp3(const uint8_t *buf, size_t len) {
    drmp3 mp3;
    if (!drmp3_init_memory(&mp3, buf, len, NULL)) {
        fail();
    }
    Audio a;
    a.rate = mp3.sampleRate;
    a.channels = mp3.channels;
    a.width = 2;
    float tmp[4096];
    size_t k;
    while ((k = drmp3_read_pcm_frames_f32(&mp3, 4096 / a.channels, tmp)) > 0) {
        put(a.data, tmp, k * a.channels, 2);
    }
    drmp3_uninit(&mp3);
    return a;
}

static Audio read_vorbis(const uint8_t *buf, size_t len) {
    int err = 0;
    stb_vorbis *v = stb_vorbis_open_memory(buf, (int)len, &err, NULL);
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
    stb_vorbis_close(v);
    return a;
}

static Audio read_opus(const uint8_t *buf, size_t len) {
    int err = 0;
    OggOpusFile *of = op_open_memory(buf, len, &err);
    if (!of) {
        fail();
    }
    Audio a;
    a.rate = 48000;
    a.channels = op_channel_count(of, -1);
    a.width = 4;
    float tmp[5760 * 2];
    int k;
    while ((k = op_read_float(of, tmp, 5760 * 2, NULL)) != 0) {
        if (k < 0) {
            op_free(of);
            fail();
        }
        put(a.data, tmp, k * a.channels, 4);
    }
    op_free(of);
    return a;
}

Audio decode(const uint8_t *buf, size_t len) {
    Audio a;
    if (len >= 12 && !memcmp(buf, "RIFF", 4) && !memcmp(buf + 8, "WAVE", 4)) {
        a = read_wav(buf, len);
    } else if (len >= 4 && !memcmp(buf, "fLaC", 4)) {
        a = read_flac(buf, len);
    } else if (len >= 40 && !memcmp(buf, "OggS", 4) && !memcmp(buf + 27 + buf[26], "OpusHead", 8)) {
        a = read_opus(buf, len);
    } else if (len >= 40 && !memcmp(buf, "OggS", 4) && !memcmp(buf + 27 + buf[26], "\x01vorbis", 7)) {
        a = read_vorbis(buf, len);
    } else if (len >= 4 && (!memcmp(buf, "ID3", 3) || (buf[0] == 0xff && (buf[1] & 0xe0) == 0xe0))) {
        a = read_mp3(buf, len);
    } else {
        fail();
    }
    if (a.data.empty() || a.channels < 1) {
        fail();
    }
    return a;
}

}
