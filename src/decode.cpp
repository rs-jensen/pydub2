#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

#define DR_WAV_IMPLEMENTATION
#define DR_FLAC_IMPLEMENTATION
#define DR_MP3_IMPLEMENTATION
#define DR_MP3_FLOAT_OUTPUT
#include "third_party/dr_wav.h"
#include "third_party/dr_flac.h"
#include "third_party/dr_mp3.h"
#define STB_VORBIS_HEADER_ONLY
#include "third_party/stb_vorbis.c"
extern "C" void crc32_init(void); // not static in our copy
#include <opusfile.h>

#include "core.h"

#ifdef _WIN32
#define fseeko _fseeki64
#define ftello _ftelli64
#endif

namespace pydub2 {

static void fail() {
    throw std::runtime_error("cant read this file");
}

// same rounding and clipping as ffmpegs float to int. nan and huge values are whatever the cpu does, like in ffmpeg
static void put(char *out, const float *f, size_t n, int width) {
    for (size_t i = 0; i < n; i++) {
        if (width == 2) {
            long v = lrintf(f[i] * 32768.0f);
            int16_t s = v > 32767 ? 32767 : v < -32768 ? -32768 : (int16_t)v;
            memcpy(out + i * 2, &s, 2);
        } else {
            long long v = llrintf(f[i] * 2147483648.0f);
            int32_t s = v > INT32_MAX ? INT32_MAX : v < INT32_MIN ? INT32_MIN : (int32_t)v;
            memcpy(out + i * 4, &s, 4);
        }
    }
}

// a file or a piece of memory
struct Input {
    FILE *f = NULL;
    const uint8_t *buf = NULL;
    uint64_t len = 0, pos = 0;
};

static size_t in_read(void *u, void *out, size_t n) {
    Input *in = (Input *)u;
    n = (size_t)std::min<uint64_t>(n, in->len - std::min(in->len, in->pos));
    if (in->f) {
        n = fread(out, 1, n, in->f);
    } else if (n) {
        memcpy(out, in->buf + in->pos, n);
    }
    in->pos += n;
    return n;
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static bool in_seek(Input *in, int64_t off, int origin) {
    int64_t at = off + (origin == SEEK_SET ? 0 : origin == SEEK_CUR ? (int64_t)in->pos : (int64_t)in->len);
    if (at < 0 || at > (int64_t)in->len || (in->f && fseeko(in->f, at, SEEK_SET))) {
        return false;
    }
    in->pos = (uint64_t)at;
    return true;
}

// a position that is too big turns negative and in_seek says no
static bool seek_to(Input *in, uint64_t at) {
    return in_seek(in, (int64_t)at, SEEK_SET);
}

static_assert(DRWAV_SEEK_CUR == SEEK_CUR && DRWAV_SEEK_END == SEEK_END && DRFLAC_SEEK_CUR == SEEK_CUR &&
              DRFLAC_SEEK_END == SEEK_END, "dr seek origins");

// the dr libs all want the same three, only the enum type differs
template <class O> static uint32_t dr_seek(void *u, int off, O origin) {
    return in_seek((Input *)u, off, (int)origin);
}

static uint32_t dr_tell(void *u, long long *at) {
    Input *in = (Input *)u;
    *at = (long long)in->pos;
    return 1;
}

struct Dec : Decoder {
    Input in;
    virtual void init() = 0;
    ~Dec() {
        if (in.f) {
            fclose(in.f);
        }
    }
};

struct Wav : Dec {
    drwav w;
    bool ok = false;
    size_t bits = 0;
    size_t frame = 0;

    ~Wav() {
        if (ok) {
            drwav_uninit(&w);
        }
    }

    void init() {
        if (!drwav_init(&w, in_read, dr_seek<drwav_seek_origin>, dr_tell, &in, NULL)) {
            fail();
        }
        ok = true;
        rate = (int)w.sampleRate;
        channels = w.channels;
        bits = w.bitsPerSample;
        frame = w.fmt.blockAlign;
        if (w.translatedFormatTag == DR_WAVE_FORMAT_PCM && (bits == 8 || bits == 16 || bits == 24 || bits == 32) && frame == channels * bits / 8) {
            width = bits == 24 ? 4 : bits / 8;
        } else if (w.translatedFormatTag == DR_WAVE_FORMAT_IEEE_FLOAT && bits == 64 && frame == channels * 8) {
            // pydub has ffmpeg turn this into 64 bit ints
            width = 8;
        } else if (w.translatedFormatTag == DR_WAVE_FORMAT_IEEE_FLOAT && frame) {
            width = 4;
            bits = 0;
            // dr_wav reads frames of this size whatever the block align says, so count them the same way
            frame = drwav_get_bytes_per_pcm_frame(&w);
        } else {
            fail();
        }
        // like ffmpeg: a data size of 0 or 0xffffffff (a pipe) runs to the end of the file, and a later data chunk wins
        uint8_t h[8];
        for (uint64_t at = w.dataChunkDataPos; w.container == drwav_container_riff && at >= 4;) {
            if (!seek_to(&in, at - 4) || in_read(&in, h, 4) < 4) {
                break;
            }
            uint64_t size = le32(h);
            size = size && size != 0xffffffff ? std::min(size, in.len - at) : in.len - at;
            w.dataChunkDataPos = at;
            w.dataChunkDataSize = w.bytesRemaining = size;
            w.totalPCMFrameCount = size / frame;
            at = 0;
            // a thousand chunks at most, past that it is junk
            uint64_t next = w.dataChunkDataPos + size + (size & 1);
            for (int i = 0; i < 1000 && next + 8 <= in.len && seek_to(&in, next) && in_read(&in, h, 8) == 8; i++) {
                if (!memcmp(h, "data", 4)) {
                    at = next + 8;
                    break;
                }
                // in 64 bits, a size near 4 GB must not wrap round to a small step
                next += 8 + (uint64_t)le32(h + 4) + (h[4] & 1u);
            }
        }
        seek_to(&in, w.dataChunkDataPos);
        // the header can claim any size, the file cant hold more
        frames = std::min<uint64_t>(w.totalPCMFrameCount, (in.len - std::min<uint64_t>(in.len, w.dataChunkDataPos)) / frame);
        exact = true;
    }

    size_t read(char *out, size_t n) {
        size_t done = 0, k;
        if (!bits) {
            float tmp[4096];
            while (done < n && (k = drwav_read_pcm_frames_f32(&w, std::min<size_t>(n - done, 4096 / channels), tmp)) > 0) {
                put(out + done * channels * 4, tmp, k * channels, 4);
                done += k;
            }
            return done * channels * 4;
        }
        if (bits != 24) {
            k = drwav_read_raw(&w, n * frame, out);
            k -= k % frame; // a data size that is not a whole number of frames leaves stray bytes
            for (size_t i = 0; bits == 64 && i < k; i += 8) {
                // ffmpegs double to s64, it saturates and nan is whatever the cpu does
                double x;
                memcpy(&x, out + i, 8);
                x *= 9223372036854775808.0;
                int64_t v = x >= 9223372036854775808.0 ? INT64_MAX : x <= -9223372036854775808.0 ? INT64_MIN : llrint(x);
                memcpy(out + i, &v, 8);
            }
            if (bits == 8) {
                for (size_t i = 0; i < k; i++) {
                    out[i] = (char)(out[i] ^ 0x80);
                }
            }
            return k;
        }
        // pydub pads the low byte with 00 or ff, not zeros
        char tmp[3 * 4096];
        n *= channels;
        while (done < n && (k = drwav_read_raw(&w, std::min<size_t>(n - done, 4096) * 3, tmp) / 3) > 0) {
            for (size_t i = 0; i < k; i++) {
                char *o = out + (done + i) * 4;
                o[0] = (int8_t)tmp[i * 3 + 2] < 0 ? '\xff' : '\0';
                memcpy(o + 1, tmp + i * 3, 3);
            }
            done += k;
        }
        done -= done % channels;
        return done * 4;
    }

    void seek(uint64_t to) {
        drwav_seek_to_pcm_frame(&w, std::min(to, frames));
    }
};

// a header can lie about the length, so only believe one the file could hold at that much compression
static bool plausible(uint64_t frames, size_t fw, uint64_t len, uint64_t ratio) {
    return frames <= (((uint64_t)64 << 20) + len * ratio) / fw;
}

struct Flac : Dec {
    drflac *f = NULL;
    bool end = false, lost = false, held = false;
    uint64_t pos = UINT64_MAX, owed = 0;
    char one[32];

    ~Flac() {
        drflac_close(f);
    }

    void init() {
        f = drflac_open(in_read, dr_seek<drflac_seek_origin>, dr_tell, &in, NULL);
        if (!f) {
            fail();
        }
        rate = (int)f->sampleRate;
        channels = f->channels;
        width = f->bitsPerSample <= 16 ? 2 : 4;
        frames = f->totalPCMFrameCount;
        exact = plausible(frames, channels * width, in.len, 20);
    }

    uint64_t first() {
        drflac_frame_header &h = f->currentFLACFrame.header;
        return h.pcmFrameNumber ? h.pcmFrameNumber : (uint64_t)h.flacFrameNumber * f->maxBlockSizeInPCMFrames;
    }

    size_t pull(size_t n, char *out) {
        size_t k = width == 2 ? drflac_read_pcm_frames_s16(f, n, (int16_t *)out) : drflac_read_pcm_frames_s32(f, n, (int32_t *)out);
        if (k) {
            pos = first() + f->currentFLACFrame.header.blockSizeInPCMFrames - f->currentFLACFrame.pcmFramesRemaining;
        }
        return k;
    }

    // like ffmpeg a frame with a bad crc stays in, silence here. a frame dr_flac gave up on is gone in both
    size_t read(char *out, size_t n) {
        size_t w = channels * width, done = 0, bad = 0;
        while (done < n && !end) {
            if (owed || held) {
                size_t k = (size_t)std::min<uint64_t>(owed ? owed : 1, n - done);
                if (owed) {
                    memset(out + done * w, 0, k * w);
                    owed -= k;
                } else {
                    memcpy(out + done * w, one, w);
                    held = false;
                }
                done += k;
            } else if (f->currentFLACFrame.pcmFramesRemaining) {
                done += pull(std::min<size_t>(n - done, f->currentFLACFrame.pcmFramesRemaining), out + done * w);
            } else if (bad < 64) {
                uint64_t was = pos, blk = f->currentFLACFrame.header.blockSizeInPCMFrames;
                if (!pull(1, one)) {
                    bad++;
                    lost = true;
                    continue;
                }
                bad = 0;
                // frame numbers count in max block size only when the frames before had that size
                bool counted = f->currentFLACFrame.header.pcmFrameNumber || blk == f->maxBlockSizeInPCMFrames;
                uint64_t gap = first() - was;
                owed = !lost && counted && was != UINT64_MAX && first() > was && gap <= 8ull * f->maxBlockSizeInPCMFrames ? gap : 0;
                lost = false;
                held = true;
            } else {
                break;
            }
        }
        return done * w;
    }

    void seek(uint64_t to) {
        end = !drflac_seek_to_pcm_frame(f, to);
        pos = UINT64_MAX;
        owed = 0;
        held = lost = false;
    }
};

static uint32_t be32(const uint8_t *h) {
    return (uint32_t)h[0] << 24 | (uint32_t)h[1] << 16 | (uint32_t)h[2] << 8 | h[3];
}

// ffmpegs header check. its parser takes no free format frames
static bool mp3_head(const uint8_t *h) {
    uint32_t v = be32(h);
    return (v & 0xffe00000) == 0xffe00000 && (v >> 19 & 3) != 1 && (v >> 17 & 3) && (v >> 12 & 15) &&
           (v >> 12 & 15) != 15 && (v >> 10 & 3) != 3;
}

static size_t mp3_size(const uint8_t *h) {
    return (size_t)(drmp3_hdr_frame_bytes(h, 0) + drmp3_hdr_padding(h));
}

static uint64_t mp3_id3(Input &in) {
    uint8_t h[10];
    uint64_t at = 0;
    // ffmpegs match: a version and flags below ff, a size of 7 bit bytes
    while (seek_to(&in, at) && in_read(&in, h, 10) == 10 && !memcmp(h, "ID3", 3) && h[3] != 255 && h[4] != 255 &&
           !((h[6] | h[7] | h[8] | h[9]) & 128)) {
        at += 10 + ((h[6] & 127u) << 21 | (h[7] & 127u) << 14 | (h[8] & 127u) << 7 | (h[9] & 127u)) + (h[5] & 16 ? 10u : 0u);
    }
    return at;
}

// ffmpeg drops the Info frame and takes the lame delay from it only right after the tags, returns where audio starts
static uint64_t mp3_tag(Input &in, uint64_t at, size_t &skip, size_t &cut, uint64_t &count) {
    std::vector<uint8_t> buf(4096);
    if (!seek_to(&in, at)) {
        return at;
    }
    size_t len = in_read(&in, buf.data(), buf.size());
    const uint8_t *f = buf.data();
    if (len < 4 || (f[1] & 6) != 2) {
        return at;
    }
    size_t n = std::min(mp3_size(f), len);
    bool mono = DRMP3_HDR_IS_MONO(f);
    // ffmpeg looks for the tag where it is in a frame without a crc
    size_t o = 4 + ((f[1] & 8) ? (mono ? 17 : 32) : (mono ? 9 : 17));
    auto word = [&](size_t q) { return q + 4 <= n ? be32(f + q) : 0u; };
    uint64_t size = 0, frames = 0;
    if (o + 8 <= n && (!memcmp(f + o, "Xing", 4) || !memcmp(f + o, "Info", 4))) {
        int flags = f[o + 7];
        o += 8;
        const size_t sizes[] = {4, 4, 100, 4};
        for (int i = 0; i < 4; i++) {
            if (flags >> i & 1) {
                if (i == 0) {
                    count = word(o);
                } else if (i == 1) {
                    size = word(o);
                }
                o += sizes[i];
            }
        }
        // a file much bigger than the tag says counts as files glued together, ffmpeg then forgets the frame count
        uint64_t have = in.len - at;
        if (size && have > size && have - size > size >> 4) {
            count = 0;
        }
        frames = count;
        if (o + 24 <= n && (!memcmp(f + o, "LAME", 4) || !memcmp(f + o, "Lavf", 4) || !memcmp(f + o, "Lavc", 4))) {
            skip = (size_t)((f[o + 21] << 4 | f[o + 22] >> 4) + 529);
            unsigned pad = (f[o + 22] & 15u) << 8 | f[o + 23];
            cut = pad > 529 ? pad - 529 : 0;
        }
    }
    if (54 <= n && !memcmp(f + 36, "VBRI", 4) && f[40] == 0 && f[41] == 1) {
        size = word(46);
        frames = word(50);
    }
    // a tag that counts neither frames nor bytes is audio to ffmpeg, its delay still counts
    return frames || size ? at + mp3_size(f) : at;
}

static int bits(const uint8_t *o, size_t p, int n) {
    int v = 0;
    for (; n--; p++) {
        v = v << 1 | (o[p / 8] >> (7 - p % 8) & 1);
    }
    return v;
}

static bool mp3_side(const uint8_t *o, size_t n, size_t &p, size_t &gr, size_t &ng, size_t &nch, size_t &step) {
    bool v1 = o[1] & 8;
    nch = (o[3] >> 6) == 3 ? 1 : 2;
    ng = v1 ? 2 : 1;
    step = v1 ? 59 : 63;
    p = (4 + (o[1] & 1 ? 0u : 2u)) * 8;
    gr = p + (v1 ? 9 + (nch == 1 ? 5 : 3) + 4 * nch : 8 + nch);
    return gr + ng * nch * step <= n * 8;
}

// ffmpeg drops a frame with big_values over 288 or a window switch to block type 0
static bool mp3_bad(const uint8_t *o, size_t n) {
    size_t p, gr, ng, nch, step;
    if (!mp3_side(o, n, p, gr, ng, nch, step)) {
        return false;
    }
    size_t sc = ng == 2 ? 4 : 9;
    for (size_t i = 0; i < ng * nch; i++) {
        size_t q = gr + i * step;
        if (bits(o, q + 12, 9) > 288 || (bits(o, q + 29 + sc, 1) && !bits(o, q + 30 + sc, 2))) {
            return true;
        }
    }
    return false;
}

// ffmpeg zeroes the granules a missing bit reservoir would feed, minimp3 drops the frame
static int mp3_fix(uint8_t *o, size_t n, int have) {
    size_t p, gr, ng, nch, step;
    if (!mp3_side(o, n, p, gr, ng, nch, step)) {
        return 0;
    }
    auto set = [&](size_t at, int w, int v) {
        for (; w--; at++) {
            o[at / 8] = (uint8_t)((o[at / 8] & ~(128 >> at % 8)) | (v >> w & 1) << (7 - at % 8));
        }
    };
    bool v1 = ng == 2;
    int mdb = bits(o, p, v1 ? 9 : 8), acc = have * 8;
    size_t z = 0;
    if (mdb <= have) {
        return 0;
    }
    for (; z < ng && acc >> 3 < mdb; z++) {
        for (size_t c = 0; c < nch; c++) {
            acc += bits(o, gr + (z * nch + c) * step, 12);
        }
    }
    int rem = (mdb - have) * 8;
    for (size_t i = 0; i < z * nch; i++) {
        size_t q = gr + i * step;
        int m = bits(o, q, 12), d = std::min(m, rem);
        rem -= d;
        set(q, 12, m - d);
        set(q + 12, 9, 0);
        set(q + 21, 8, 0);
        set(q + 29, v1 ? 4 : 9, 0);
    }
    set(p, v1 ? 9 : 8, have);
    return (int)z;
}

// the start of a container that can hold mp3 frames in its payload
static bool mp3_container(const uint8_t *h, uint64_t len) {
    if (len >= 262 && !memcmp(h + 257, "ustar", 5)) {
        return true;
    }
    for (const char *m : {"RIFF", "RF64", "FORM", "caff", "OggS", "fLaC", "wvpk", "MAC ", "FLV\1", ".snd", "\x1a\x45\xdf\xa3", "\x30\x26\xb2\x75", "\0\0\1\xba", "PK\3\4", "%PDF"}) {
        if (!memcmp(h, m, 4)) {
            return true;
        }
    }
    for (const char *m : {"ftyp", "moov", "mdat", "free", "wide", "skip", "styp", "moof", "sidx"}) {
        if (!memcmp(h + 4, m, 4)) {
            return true;
        }
    }
    return false;
}

struct Mp3 : Dec {
    // a frame that gets to ffmpegs decoder. it drops a bad one, but the end of that goes in the bit reservoir
    struct Frame {
        uint64_t at;
        size_t n;
        bool bad;
    };
    std::vector<Frame> fr;
    size_t k = 0;
    drmp3dec dec;
    uint8_t head[4];
    bool eof = false;
    size_t skip = 0, cut = 0, spf = 0;
    // decoded floats from frame start on, pos is the next frame to hand out
    std::vector<float> buf;
    uint64_t start = 0, pos = 0;
    // padding ffmpeg drops in the middle, in decoded frames
    uint64_t hole = 0, hole_at = 0;

    void init() {
        uint64_t count = 0, len = in.len, at = mp3_id3(in), off = 0;
        std::vector<uint8_t> b;
        auto load = [&](uint64_t p) {
            if (p < off || std::min(len, p + 4096) > off + b.size()) {
                b.resize(65536);
                if (!seek_to(&in, p)) {
                    fail();
                }
                b.resize(in_read(&in, b.data(), b.size()));
                off = p;
                if (std::min(len, p + 4096) > off + b.size()) {
                    fail();
                }
            }
            return (const uint8_t *)b.data() + (p - off);
        };
        const uint8_t *h0 = at + 4 <= len ? load(at) : NULL;
        bool sync = h0 && h0[0] == 0xff && (h0[1] & 0xe0) == 0xe0, valid = h0 && mp3_head(h0);
        if (!at && !sync && (len < 8 || mp3_container(h0, len))) {
            fail();
        }
        auto chain = [&](uint64_t q, int n) {
            uint32_t a = 0;
            for (int k = 0; k < n; k++) {
                if (q + 4 > len) {
                    return false;
                }
                const uint8_t *h = load(q);
                if (!mp3_head(h) || (k && ((a ^ be32(h)) & 0xfffe0ccf))) {
                    return false;
                }
                a = be32(h);
                q += mp3_size(h);
            }
            return true;
        };
        // like ffmpegs demuxer: first place with two headers in a row, after 64k its parser starts at the beginning
        auto find = [&](uint64_t from) {
            for (uint64_t i = 0; i < 65536; i++) {
                if (from + i + 4 > len) {
                    fail();
                }
                const uint8_t *h = load(from + i);
                if (mp3_head(h) && chain(from + i, 2)) {
                    return from + i;
                }
            }
            return from;
        };
        uint64_t first = find(at);
        // a file that starts with junk needs four frames in a row, only in the first 1 MiB, junk past that goes to ffmpeg
        for (uint64_t i = 0; !at && !sync && !chain(i, 4); i++) {
            if (i >= (1 << 20)) {
                fail();
            }
        }
        uint64_t next = valid ? mp3_tag(in, at, skip, cut, count) : at;
        if (next != at) {
            first = find(next);
        }
        // like its parser: the 4 byte window starts empty after a frame, junk other than zeros drops the next frame
        uint64_t t = 0, e = count * drmp3_hdr_frame_samples(load(first)), drop = 0, out = 0, dur = 0;
        bool none = true, after = false;
        int gaps = 0, heads = 0;
        uint32_t last = 0;
        cut = e < cut ? 0 : cut;
        for (uint64_t p = first; p < len;) {
            uint64_t h = p;
            bool zero = true;
            for (; h + 4 <= len && !mp3_head(load(h)); h++) {
                zero = zero && !*load(h);
            }
            if (h + 4 > len) {
                break;
            }
            gaps += h != p && !none;
            uint8_t f[4096];
            size_t n = mp3_size(load(h)), got = (size_t)std::min<uint64_t>(n, len - h);
            memcpy(f, load(h), got);
            memset(f + got, 0, n - got);
            bool bad = (f[1] & 6) == 2 && mp3_bad(f, n), keep = zero && !bad;
            if (keep && none) {
                memcpy(head, f, 4);
                none = false;
            }
            // ffmpeg resamples to the rate of the first frame, that we leave to it
            if (keep && !drmp3_hdr_compare(head, f)) {
                fail();
            }
            if (zero) {
                fr.push_back({h, n, bad});
            }
            // the padding goes by the time of all frames, dropped ones too, and only the first carries the delay. The
            // parser keeps the old duration for two frames after junk and three after a new version, layer or rate
            heads = last && ((be32(f) ^ last) & 0xfffe0c00) ? -3 : h != p ? -2 : heads;
            last = be32(f);
            dur = ++heads > 0 ? drmp3_hdr_frame_samples(f) : dur;
            if (keep && cut && t < e && t + dur > e - cut) {
                uint64_t d = std::min(t + dur - (e - cut), dur);
                hole_at = drop ? hole_at : out + dur - d;
                drop += d;
            }
            out += keep ? dur : 0;
            after = after || (keep && t >= e);
            skip = t || keep ? skip : 0;
            t += dur;
            p = h + n;
        }
        // a junk start with frames cut up again and again is another container, ts for one
        if (none || (head[1] & 6) != 2 || (!at && !sync && gaps > 2)) {
            fail();
        }
        rate = (int)drmp3_hdr_sample_rate_hz(head);
        channels = DRMP3_HDR_IS_MONO(head) ? 1 : 2;
        width = 2;
        spf = drmp3_hdr_frame_samples(head);
        uint64_t n = 0;
        for (Frame &f : fr) {
            n += f.bad ? 0 : spf;
        }
        // where the padding is not at the end ffmpeg drops it in the middle
        cut = after ? 0 : drop;
        hole = after && hole_at >= skip ? drop : 0;
        frames = n > skip + cut + hole ? n - skip - cut - hole : 0;
        exact = true;
        pos = skip;
        memset(&dec, 0, sizeof(dec));
    }

    size_t step(float *out) {
        Frame f = fr[k++];
        uint8_t b[4096];
        if (in.pos != f.at && !seek_to(&in, f.at)) {
            fail();
        }
        size_t got = in_read(&in, b, f.n);
        // ffmpeg reads zeros past the cut and stops the huffman data there
        memset(b + got, 0, f.n - got);
        dec.missing = (int)(f.n - got);
        if (f.bad) {
            size_t r = std::min<size_t>(511, f.n - 4);
            memcpy(dec.reserv_buf, b + f.n - r, r);
            dec.reserv = (int)r;
            return 0;
        }
        dec.zeroed = mp3_fix(b, f.n, dec.reserv);
        // so dr_mp3 takes the frame as it is and keeps its state
        memcpy(dec.header, b, 4);
        drmp3dec_frame_info fi;
        float pcm[1152 * 2];
        if (!drmp3dec_decode_frame(&dec, b, (int)f.n, pcm, &fi)) {
            memset(pcm, 0, sizeof(pcm));
        }
        // ffmpeg keeps the channels of the first frame and mixes the others like swresample
        size_t c = DRMP3_HDR_IS_MONO(b) ? 1 : 2;
        if (c == channels) {
            memcpy(out, pcm, spf * c * sizeof(float));
        }
        for (size_t i = 0; c != channels && i < spf; i++) {
            if (c == 2) {
                out[i] = (pcm[2 * i] + pcm[2 * i + 1]) * 0.5f;
            } else {
                out[2 * i] = out[2 * i + 1] = pcm[i] * 0.70710678f;
            }
        }
        return spf;
    }

    // j and to count decoded frames. end is where the padding starts, if known
    void convert(char *out, uint64_t j, uint64_t to, [[maybe_unused]] uint64_t end) {
        size_t c = channels;
        while (j < to) {
            uint64_t f = j / spf * spf;
            uint64_t stop = std::min<uint64_t>(to, f + spf);
#if defined(__aarch64__) || defined(_M_ARM64)
            // ffmpeg on arm converts each decoded frame with neon in blocks of 16, the rest like put
            uint64_t lo = std::max<uint64_t>(skip, f), hi = std::min<uint64_t>(f + spf, end);
            uint64_t simd = lo + ((hi - lo) & ~(uint64_t)15);
            for (; j < std::min(stop, simd); j++) {
                for (size_t i = 0; i < c; i++) {
                    double q = std::trunc(buf[(j - start) * c + i] * 2147483648.0);
                    int32_t v = q >= 2147483647.0 ? INT32_MAX : q <= -2147483648.0 ? INT32_MIN : (int32_t)q;
                    int16_t s = c > 1 ? (int16_t)(v >> 16) : (int16_t)std::min<int64_t>(((int64_t)v + 32768) >> 16, 32767);
                    memcpy(out, &s, 2);
                    out += 2;
                }
            }
#elif defined(__x86_64__) || defined(_M_X64)
            // ffmpeg on x86 converts each decoded frame with sse in blocks of 16, which gives -32768 for a sample past the
            // int32 range. Only damaged frames get that loud
            uint64_t lo = std::max<uint64_t>(skip, f), hi = std::min<uint64_t>(f + spf, end);
            uint64_t simd = std::min(stop, lo + ((hi - lo) & ~(uint64_t)15));
            if (j < simd) {
                const float *x = &buf[(j - start) * c];
                size_t m = (size_t)(simd - j) * c;
                put(out, x, m, 2);
                for (size_t i = 0; i < m; i++) {
                    if (std::fabs(x[i] * 32768.0f) >= 2147483648.0f) {
                        memcpy(out + i * 2, "\0\x80", 2);
                    }
                }
                out += m * 2;
                j = simd;
            }
#endif
            put(out, &buf[(j - start) * c], (stop - j) * c, 2);
            out += (stop - j) * c * 2;
            j = stop;
        }
    }

    size_t read(char *out, size_t n) {
        size_t c = channels, done = 0;
        while (done < n) {
            uint64_t have = start + buf.size() / c;
            uint64_t end = have - std::min<uint64_t>(have, cut);
            // a frame can only be converted once we know if the padding starts in it
            uint64_t lim = eof ? end : end / spf * spf, stop = eof ? end : UINT64_MAX;
            if (hole && pos == hole_at) {
                pos += hole;
            }
            if (hole && pos < hole_at) {
                lim = std::min(lim, hole_at);
                stop = std::min(stop, hole_at);
            }
            if (pos < lim) {
                size_t m = (size_t)std::min<uint64_t>(n - done, lim - pos);
                convert(out + done * c * 2, pos, pos + m, stop);
                pos += m;
                done += m;
                continue;
            }
            if (eof) {
                break;
            }
            size_t drop = (size_t)std::min<uint64_t>(pos - start, buf.size() / c);
            buf.erase(buf.begin(), buf.begin() + (ptrdiff_t)(drop * c));
            start += drop;
            size_t old = buf.size(), got = 0;
            buf.resize(old + 1152 * 2);
            while (!got && k < fr.size()) {
                got = step(&buf[old]);
            }
            buf.resize(old + got * c);
            eof = !got;
        }
        return done * c * 2;
    }

    void seek(uint64_t to) {
        to = std::min(to, frames) + skip;
        to += hole && to >= hole_at ? hole : 0;
        uint64_t want = to / spf, g = 0;
        size_t at = 0;
        for (; at < fr.size() && (fr[at].bad || g++ < want); at++) {
        }
        // the overlap comes from the last kept frame, its reservoir from up to 511 bytes of main data before it
        k = at;
        while (k > 0 && fr[--k].bad) {
        }
        size_t front = spf == 1152 ? 38 : 23;
        for (size_t b = 0, i = 0; k > 0 && b < 511 + front * i; i++) {
            b += fr[--k].n;
        }
        memset(&dec, 0, sizeof(dec));
        float tmp[1152 * 2];
        while (k < at) {
            step(tmp);
        }
        buf.clear();
        start = want * spf;
        pos = to;
        eof = false;
    }
};

// one whole ogg page onto the end of out. 0 at a clean end, -1 on anything else (a cut page still goes on)
static int ogg_page(Input &in, std::string &out) {
    uint8_t h[27 + 255];
    size_t k = in_read(&in, h, 27);
    if (!k) {
        return 0;
    }
    if (k < 27 || memcmp(h, "OggS", 4) || in_read(&in, h + 27, h[26]) < h[26]) {
        return -1;
    }
    size_t body = 0;
    for (int s = 0; s < h[26]; s++) {
        body += h[27 + s];
    }
    size_t at = out.size();
    out.append((const char *)h, 27 + h[26]);
    out.resize(out.size() + body);
    k = in_read(&in, &out[at + 27 + h[26]], body);
    if (k < body) {
        out.resize(at + 27 + h[26] + k);
        return -1;
    }
    return 1;
}

struct Packet {
    uint8_t b;
    int page;
    bool eos;
    int64_t gran;
};

struct Page {
    uint64_t at;
    int64_t gran;
};

// the length ffmpeg gives, worked out like its ogg demuxer and vorbis parser do. -1 if unsure, -2 for a chained file
// or one ffmpeg cannot open
static int64_t vorbis_len(Input &in, std::vector<Page> &pages) {
    std::string head[3];
    std::vector<Packet> pk;
    int count = 0;
    size_t plen = 0;
    uint8_t first = 0;
    std::string pg;
    char serial[4];
    for (int page = 0;; page++) {
        uint64_t at = in.pos;
        pg.clear();
        int r = ogg_page(in, pg);
        if (!r) {
            break;
        }
        const uint8_t *buf = (const uint8_t *)pg.data();
        // ffmpeg drops a page cut off by the end of the file and ends there
        if (r < 0 && in.pos == in.len) {
            break;
        }
        if (r < 0) {
            return -1;
        }
        if (page && (memcmp(buf + 14, serial, 4) || buf[5] & 2)) {
            return -2;
        }
        memcpy(serial, buf + 14, 4);
        int64_t gran;
        memcpy(&gran, buf + 6, 8);
        pages.push_back({at, gran});
        size_t n = buf[26], j = 27 + n;
        for (size_t s = 0; s < n; s++) {
            size_t k = buf[27 + s];
            if (count < 3) {
                head[count].append((const char *)buf + j, k);
            } else if (!plen && k) {
                first = buf[j];
            }
            plen += k;
            j += k;
            if (k < 255) {
                if (count >= 3 && plen && !(first & 1)) {
                    pk.push_back({first, page, (buf[5] & 4) != 0, gran});
                }
                count++;
                plen = 0;
            }
        }
    }
    // ffmpeg opens the file only once it has read a whole page with the end of the first audio packet
    if (pk.empty()) {
        return -2;
    }
    if (head[0].size() < 30 || head[2].empty()) {
        return -1;
    }
    int bs[2] = {1 << (head[0][28] & 15), 1 << (head[0][28] >> 4 & 15)};
    // ffmpeg reads the mode block flags backwards from the end of the setup header
    const std::string &s = head[2];
    size_t nb = s.size() * 8, p = 0;
    auto bit = [&](size_t q) { return (uint8_t)s[s.size() - 1 - q / 8] >> (7 - q % 8) & 1; };
    auto get = [&](int n) {
        int v = 0;
        while (n--) {
            v = v << 1 | bit(p++);
        }
        return v;
    };
    while (nb - p > 97 && !bit(p)) {
        p++;
    }
    if (nb - p <= 97) {
        return -1;
    }
    size_t start = ++p;
    int nm = 0, modes = 0;
    while (nb - p >= 97) {
        if (get(8) > 63 || get(16) || get(16)) {
            break;
        }
        p++;
        if (++nm > 64) {
            break;
        }
        size_t back = p;
        if (get(6) + 1 == nm) {
            modes = nm;
        }
        p = back;
    }
    if (modes < 1 || modes > 63) {
        return -1;
    }
    int flag[64];
    p = start;
    for (int m = modes - 1; m >= 0; m--) {
        p += 40;
        flag[m] = bit(p++);
    }
    int b = 0;
    while ((modes - 1) >> b) {
        b++;
    }
    int mask = ((1 << b) - 1) << 1, pmask = (mask | 1) + 1;
    auto mode = [&](uint8_t c) { return modes == 1 ? 0 : (c & mask) >> 1; };
    for (Packet &x : pk) {
        if (mode(x.b) >= modes) {
            return -1;
        }
    }
    auto dur = [&](int &prev, uint8_t c) {
        int m = mode(c);
        if (flag[m]) {
            prev = bs[c & pmask ? 1 : 0];
        }
        int cur = bs[flag[m]];
        int d = (prev + cur) >> 2;
        prev = cur;
        return d;
    };
    int prev = bs[0], d = 0;
    // the granules are any 64 bits, so skip wraps instead of overflowing
    int64_t total = 0;
    uint64_t skip = 0 - (uint64_t)pk.back().gran;
    for (size_t k = 0; k < pk.size(); k++) {
        d = dur(prev, pk[k].b);
        total += k ? d : 0;
    }
    if (!pk.back().eos || pk.size() < 2) {
        return total;
    }
    if (pk[0].eos) {
        prev = bs[0];
        for (Packet &x : pk) {
            skip += dur(prev, x.b);
        }
    } else {
        prev = bs[0];
        for (Packet &x : pk) {
            int e = dur(prev, x.b);
            skip += x.page == pk.back().page ? e : 0;
            if (x.page < pk.back().page) {
                skip = (uint64_t)x.gran - (uint64_t)pk.back().gran;
            }
        }
    }
    return skip && skip <= (uint64_t)d ? total - (int64_t)skip : total;
}

// stb in push mode, so we can feed it from a file and fix the last page on the way
struct Vorbis : Dec {
    stb_vorbis *v = NULL;
    std::unique_ptr<char[]> arena; // stb allocates whatever the setup header asks for, this caps it
    int64_t want = -1;
    std::vector<Page> pages;
    std::string win;
    size_t at = 0;
    uint64_t woff = 0; // where win starts in the file
    bool eof = false;
    std::vector<float> left;
    size_t lpos = 0;
    // pos is where left[lpos] sits in the output. after a seek it is unknown until stb has read past sync
    uint64_t pos = 0, target = 0, sync = 0;

    ~Vorbis() {
        if (v) {
            stb_vorbis_close(v);
        }
    }

    bool more() {
        if (eof) {
            return false;
        }
        win.erase(0, at);
        woff += at;
        at = 0;
        uint64_t off = in.pos;
        size_t old = win.size();
        int r = ogg_page(in, win);
        eof = r <= 0;
        if (r > 0 && want >= 0 && off == pages.back().at) {
            // push the last granule out so stb keeps the padding ffmpeg may keep too
            uint64_t gran;
            memcpy(&gran, &win[old + 6], 8);
            gran += 1 << 24;
            memcpy(&win[old + 6], &gran, 8);
        }
        return win.size() > old;
    }

    void restart() {
        if (v) {
            stb_vorbis_close(v);
            v = NULL;
        }
        seek_to(&in, 0);
        win.clear();
        at = woff = 0;
        eof = false;
        left.clear();
        lpos = 0;
        pos = sync = 0;
        size_t scan = 0, ends = 0;
        while (!v) {
            if (!more()) {
                fail();
            }
            // stb parses all of win again on each try, so only try once the three header packets are in
            while (scan + 27 <= win.size() && scan + 27 + (uint8_t)win[scan + 26] <= win.size()) {
                size_t nseg = (uint8_t)win[scan + 26], body = 0;
                for (size_t s = 0; s < nseg; s++) {
                    body += (uint8_t)win[scan + 27 + s];
                    ends += (uint8_t)win[scan + 27 + s] < 255;
                }
                scan += 27 + nseg + body;
            }
            if (ends < 3 && !eof) {
                continue;
            }
            int used, err;
            if (!arena) {
                arena.reset(new char[32 << 20]);
            }
            static std::once_flag crc;
            std::call_once(crc, crc32_init);
            stb_vorbis_alloc mem = {arena.get(), 32 << 20};
            v = stb_vorbis_open_pushdata((const uint8_t *)win.data(), (int)win.size(), &used, &err, &mem);
            if (v) {
                at = (size_t)used;
            } else if (err != VORBIS_need_more_data) {
                fail();
            }
        }
    }

    void init() {
        want = vorbis_len(in, pages);
        if (want == -2) {
            fail();
        }
        restart();
        stb_vorbis_info info = stb_vorbis_get_info(v);
        rate = (int)info.sample_rate;
        channels = (size_t)info.channels;
        width = 4;
        frames = want > 0 ? (uint64_t)want : 0;
        exact = plausible(frames, channels * width, in.len, 64);
        if (!want) {
            fail();
        }
    }

    // decode the next packet into left, false at the end
    bool frame() {
        for (;;) {
            int k = 0;
            float **o;
            int used = stb_vorbis_decode_frame_pushdata(v, (const uint8_t *)win.data() + at, (int)(win.size() - at), NULL, &o, &k);
            at += (size_t)used;
            if (!used && !k && !more()) {
                return false;
            }
            if (k <= 0 || (sync && woff + at < sync)) {
                continue;
            }
            size_t nk = (size_t)k;
            if (sync) {
                pos = (uint64_t)stb_vorbis_get_sample_offset64(v) - nk;
                sync = 0;
            }
            size_t c = channels;
            left.resize(nk * c);
            for (size_t i = 0; i < nk; i++) {
                for (size_t j = 0; j < c; j++) {
                    left[i * c + j] = o[c <= 8 ? order[c - 1][j] : j][i];
                }
            }
            lpos = pos < target ? (size_t)std::min<uint64_t>(nk, target - pos) : 0;
            pos += lpos;
            if (lpos < nk) {
                return true;
            }
        }
    }

    size_t read(char *out, size_t n) {
        size_t c = channels, done = 0;
        while (done < n && (want < 0 || pos < (uint64_t)want)) {
            size_t have = left.size() / c - lpos;
            if (!have) {
                if (!frame()) {
                    break;
                }
                continue;
            }
            size_t k = std::min(n - done, have);
            if (want >= 0) {
                k = (size_t)std::min<uint64_t>(k, (uint64_t)want - pos);
            }
            put(out + done * c * 4, &left[lpos * c], k * c, 4);
            done += k;
            lpos += k;
            pos += k;
        }
        return done * c * 4;
    }

    void seek(uint64_t to) {
        // restart after page q, positions are known again once stb is past page q + 1
        size_t q = 0;
        for (size_t i = 1; want >= 0 && i + 2 < pages.size(); i++) {
            if (pages[i].gran > 0 && pages[i + 1].gran >= 0 && (uint64_t)pages[i + 1].gran + 8192 <= to) {
                q = i;
            }
        }
        if (!q) {
            restart();
        } else {
            stb_vorbis_flush_pushdata(v);
            seek_to(&in, pages[q].at);
            win.clear();
            at = 0;
            woff = pages[q].at;
            eof = false;
            left.clear();
            lpos = 0;
            pos = 0;
            sync = pages[q + 2].at;
        }
        target = to;
    }
};

static int op_read(void *u, unsigned char *out, int n) {
    return (int)in_read(u, out, (size_t)n);
}

static int op_seek(void *u, opus_int64 off, int whence) {
    return in_seek((Input *)u, off, whence) ? 0 : -1;
}

static opus_int64 op_tell(void *u) {
    return (opus_int64)((Input *)u)->pos;
}

struct Opus : Dec {
    OggOpusFile *of = NULL;
    bool end = false;

    ~Opus() {
        if (of) {
            op_free(of);
        }
    }

    void init() {
        OpusFileCallbacks cb = {op_read, op_seek, op_tell, NULL};
        of = op_open_callbacks(&in, &cb, NULL, 0, NULL);
        if (!of) {
            fail();
        }
        chained = op_link_count(of) > 1;
        rate = 48000;
        channels = (size_t)op_channel_count(of, -1);
        width = 4;
        ogg_int64_t n = op_pcm_total(of, -1);
        if (!n) {
            fail();
        }
        frames = n > 0 ? (uint64_t)n : 0;
        exact = plausible(frames, channels * width, in.len, 64);
    }

    size_t read(char *out, size_t n) {
        float tmp[5760 * 2];
        size_t c = channels, done = 0;
        int k, link;
        while (!end && done < n && (k = op_read_float(of, tmp, (int)(std::min<size_t>(n - done, 5760 * 2 / c) * c), &link)) != 0) {
            // a chained file can change channel count, k is sized for the new one
            if (k < 0 || (size_t)op_channel_count(of, link) != channels) {
                fail();
            }
            size_t got = (size_t)k;
            if (c > 2 && op_head(of, link)->mapping_family == 1) {
                float f[8];
                for (size_t i = 0; i < got; i++) {
                    for (size_t j = 0; j < c; j++) {
                        f[j] = tmp[i * c + order[c - 1][j]];
                    }
                    memcpy(&tmp[i * c], f, c * sizeof(float));
                }
            }
            put(out + done * c * 4, tmp, got * c, 4);
            done += got;
        }
        return done * c * 4;
    }

    void seek(uint64_t to) {
        // libopus needs about half a second to decode the same bits as a read from the start, give it two
        uint64_t back = std::min<uint64_t>(to, 96000);
        end = op_pcm_seek(of, (ogg_int64_t)(to - back)) != 0;
        std::vector<char> tmp(4096 * channels * 4);
        while (back && !end) {
            size_t k = read(tmp.data(), (size_t)std::min<uint64_t>(back, 4096));
            end = !k;
            back -= k / channels / 4;
        }
    }
};

// the first packet sits after the page header and its segment table
static bool ogg_has(const uint8_t *buf, size_t len, const char *magic, size_t n) {
    return len >= 27 && len >= 27 + buf[26] + n && !memcmp(buf, "OggS", 4) && !memcmp(buf + 27 + buf[26], magic, n);
}

static std::unique_ptr<Decoder> detect(Input in) {
    uint8_t h[512];
    size_t len = in_read(&in, h, sizeof(h));
    seek_to(&in, 0);
    std::unique_ptr<Dec> d;
    if (len >= 12 && (!memcmp(h, "RIFF", 4) || !memcmp(h, "RF64", 4)) && !memcmp(h + 8, "WAVE", 4)) {
        d.reset(new Wav);
    } else if (len >= 4 && !memcmp(h, "fLaC", 4)) {
        d.reset(new Flac);
    } else if (ogg_has(h, len, "OpusHead", 8)) {
        d.reset(new Opus);
    } else if (ogg_has(h, len, "\x01vorbis", 7)) {
        d.reset(new Vorbis);
    } else {
        d.reset(new Mp3);
    }
    d->in = in;
    d->init();
    if (d->channels < 1 || d->channels > 65535 || d->rate < 1) {
        fail();
    }
    return d;
}

std::unique_ptr<Decoder> open(const uint8_t *buf, size_t len) {
    Input in;
    in.buf = buf;
    in.len = len;
    return detect(in);
}

std::unique_ptr<Decoder> open(const std::filesystem::path &path) {
    Input in;
#ifdef _WIN32
    in.f = _wfopen(path.c_str(), L"rb");
#else
    in.f = fopen(path.c_str(), "rb");
#endif
    if (!in.f) {
        throw std::runtime_error("cant open this file");
    }
    setvbuf(in.f, NULL, _IOFBF, 1 << 16);
    int64_t len = fseeko(in.f, 0, SEEK_END) ? -1 : ftello(in.f);
    if (len < 0 || fseeko(in.f, 0, SEEK_SET)) {
        fclose(in.f);
        throw std::runtime_error("cant open this file");
    }
    in.len = (uint64_t)len;
    return detect(in);
}

}
