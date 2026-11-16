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

// ffmpeg hands 3 to 8 vorbis channels out in wav order, out[i] is vorbis channel order[n - 1][i]
static const uint8_t order[8][8] = {
    {0}, {0, 1}, {0, 2, 1}, {0, 1, 2, 3}, {0, 2, 1, 3, 4}, {0, 2, 1, 5, 3, 4}, {0, 2, 1, 6, 5, 3, 4}, {0, 2, 1, 7, 5, 6, 3, 4},
};

// a file or a piece of memory. base hides bytes at the start from the decoder, tail goes after the end
// and fix replaces the bytes at fix_at
struct Input {
    FILE *f = NULL;
    const uint8_t *buf = NULL;
    uint64_t len = 0, pos = 0, base = 0, fix_at = 0;
    std::string tail, fix;
};

static size_t in_read(void *u, void *out, size_t n) {
    Input *in = (Input *)u;
    n = (size_t)std::min<uint64_t>(n, in->len + in->tail.size() - in->pos);
    size_t k = (size_t)std::min<uint64_t>(n, in->len - std::min(in->len, in->pos));
    if (in->f) {
        k = fread(out, 1, k, in->f);
        n = k < n && in->pos + k < in->len ? k : n;
    } else if (k) {
        memcpy(out, in->buf + in->pos, k);
    }
    if (n > k) {
        memcpy((char *)out + k, &in->tail[in->pos + k - in->len], n - k);
    }
    uint64_t a = std::max(in->pos, in->fix_at), b = std::min(in->pos + n, in->fix_at + in->fix.size());
    if (a < b) {
        memcpy((char *)out + (a - in->pos), &in->fix[a - in->fix_at], b - a);
    }
    in->pos += n;
    return n;
}

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static bool in_seek(Input *in, int64_t off, int origin) {
    int64_t at = off + (origin == SEEK_SET ? (int64_t)in->base : origin == SEEK_CUR ? (int64_t)in->pos : (int64_t)(in->len + in->tail.size()));
    if (at < (int64_t)in->base || at > (int64_t)(in->len + in->tail.size()) || (in->f && fseeko(in->f, std::min(at, (int64_t)in->len), SEEK_SET))) {
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
              DRFLAC_SEEK_END == SEEK_END && DRMP3_SEEK_CUR == SEEK_CUR && DRMP3_SEEK_END == SEEK_END, "dr seek origins");

// the dr libs all want the same three, only the enum type differs
template <class O> static uint32_t dr_seek(void *u, int off, O origin) {
    return in_seek((Input *)u, off, (int)origin);
}

static uint32_t dr_tell(void *u, long long *at) {
    Input *in = (Input *)u;
    *at = (long long)(in->pos - in->base);
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
                next += 8 + le32(h + 4) + (h[4] & 1u);
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

struct Flac : Dec {
    drflac *f = NULL;
    bool end = false;

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
    }

    size_t read(char *out, size_t n) {
        if (end) {
            return 0;
        }
        size_t k = width == 2 ? drflac_read_pcm_frames_s16(f, n, (int16_t *)out) : drflac_read_pcm_frames_s32(f, n, (int32_t *)out);
        return k * channels * width;
    }

    void seek(uint64_t to) {
        end = !drflac_seek_to_pcm_frame(f, to);
    }
};

// where the audio ends, before an APE tag and an ID3v1 tag. dr_mp3 reads the APE size from the wrong place
static uint64_t mp3_strip(Input &in) {
    uint64_t end = in.len;
    uint8_t t[32];
    if (end >= 128 && seek_to(&in, end - 128) && in_read(&in, t, 3) == 3 && !memcmp(t, "TAG", 3)) {
        end -= 128;
    }
    if (end >= 32 && seek_to(&in, end - 32) && in_read(&in, t, 32) == 32 && !memcmp(t, "APETAGEX", 8)) {
        uint64_t n = le32(t + 12) + (t[23] & 128 ? 32u : 0u);
        end -= std::min(n, end);
    }
    return end;
}

// dr_mp3 gives up on a short Info frame, so find it here. ffmpeg drops the frame and the lame delay and padding
// count is the number of frames the tag says the file has
static uint64_t mp3_tag(Input &in, uint64_t full, size_t &skip, size_t &cut, uint64_t &count) {
    uint8_t h[10];
    uint64_t at = 0;
    while (seek_to(&in, at) && in_read(&in, h, 10) == 10 && !memcmp(h, "ID3", 3)) {
        at += 10 + ((h[6] & 127u) << 21 | (h[7] & 127u) << 14 | (h[8] & 127u) << 7 | (h[9] & 127u)) + (h[5] & 16 ? 10u : 0u);
    }
    std::vector<uint8_t> buf(16384);
    if (!seek_to(&in, at)) {
        return 0;
    }
    size_t len = in_read(&in, buf.data(), buf.size());
    // ffmpeg drops the Info frame after junk too, but then keeps the delay
    size_t junk = 0;
    while (junk < len && buf[junk] != 0xff) {
        junk++;
    }
    const uint8_t *f = buf.data() + junk;
    len -= junk;
    if (len < 4) {
        return 0;
    }
    drmp3dec dec;
    drmp3dec_init(&dec);
    drmp3dec_frame_info fi;
    if (!drmp3dec_decode_frame(&dec, f, (int)len, NULL, &fi)) {
        return 0;
    }
    if (fi.layer != 3) {
        fail();
    }
    int side = (f[1] & 8) ? (fi.channels == 1 ? 17 : 32) : (fi.channels == 1 ? 9 : 17);
    int o = 4 + (f[1] & 1 ? 0 : 2) + side;
    if (o + 8 > fi.frame_bytes || (memcmp(f + o, "Xing", 4) && memcmp(f + o, "Info", 4))) {
        return 0;
    }
    int flags = f[o + 7];
    o += 8;
    auto be32 = [&](int q) { return q + 4 <= fi.frame_bytes ? (uint32_t)f[q] << 24 | (uint32_t)f[q + 1] << 16 | (uint32_t)f[q + 2] << 8 | f[q + 3] : 0u; };
    uint64_t size = 0;
    const int sizes[] = {4, 4, 100, 4};
    for (int i = 0; i < 4; i++) {
        if (flags >> i & 1) {
            if (i == 0) {
                count = be32(o);
            } else if (i == 1) {
                size = be32(o);
            }
            o += sizes[i];
        }
    }
    // a file much bigger than the tag says counts as files glued together, ffmpeg then forgets the frame count
    uint64_t have = full - at - junk;
    if (size && have > size && have - size > size >> 4) {
        count = 0;
    }
    if (!junk && o + 24 <= fi.frame_bytes && (!memcmp(f + o, "LAME", 4) || !memcmp(f + o, "Lavf", 4) || !memcmp(f + o, "Lavc", 4))) {
        skip = (size_t)((f[o + 21] << 4 | f[o + 22] >> 4) + 529);
        unsigned pad = (f[o + 22] & 15u) << 8 | f[o + 23];
        cut = pad > 529 ? pad - 529 : 0;
    }
    return at + junk + (uint64_t)fi.frame_bytes;
}

// minimp3 drops a frame with a missing bit reservoir, ffmpeg zeroes the granules in the missing part. so do that here, false if nothing is missing
static bool mp3_fix(uint8_t *o, int n, int have) {
    if (n < 4) {
        return false;
    }
    auto get = [&](size_t p, int bits) {
        int v = 0;
        for (; bits--; p++) {
            v = v << 1 | (o[p / 8] >> (7 - p % 8) & 1);
        }
        return v;
    };
    auto set = [&](size_t p, int bits, int v) {
        for (; bits--; p++) {
            o[p / 8] = (uint8_t)((o[p / 8] & ~(128 >> p % 8)) | (v >> bits & 1) << (7 - p % 8));
        }
    };
    bool v1 = o[1] & 8;
    size_t nch = (o[3] >> 6) == 3 ? 1 : 2, ng = v1 ? 2 : 1, step = v1 ? 59 : 63;
    size_t p = (4 + (o[1] & 1 ? 0 : 2)) * 8;
    size_t gr = p + (v1 ? 9 + (nch == 1 ? 5 : 3) + 4 * nch : 8 + nch);
    if (gr + (ng * nch - 1) * step + 29 + (v1 ? 4 : 9) > (size_t)n * 8) {
        return false;
    }
    int mdb = get(p, v1 ? 9 : 8), acc = have * 8;
    size_t z = 0;
    if (mdb <= have) {
        return false;
    }
    for (; z < ng && acc >> 3 < mdb; z++) {
        for (size_t c = 0; c < nch; c++) {
            acc += get(gr + (z * nch + c) * step, 12);
        }
    }
    int rem = (mdb - have) * 8;
    for (size_t i = 0; i < z * nch; i++) {
        size_t q = gr + i * step;
        int m = get(q, 12), d = std::min(m, rem);
        rem -= d;
        set(q, 12, m - d);
        set(q + 12, 9, 0);
        set(q + 21, 8, 0);
        set(q + 29, v1 ? 4 : 9, 0);
    }
    set(p, v1 ? 9 : 8, have);
    return true;
}

// minimp3 drops the last frame when a tag follows it or it is cut short, ffmpeg decodes it. so end the input after that frame
static void mp3_end(Input &in) {
    std::vector<uint8_t> buf(1 << 16);
    drmp3dec dec;
    drmp3dec_init(&dec);
    drmp3dec_frame_info fi;
    size_t have = 0, at = 0;
    uint64_t off = in.base, last = 0;
    uint8_t hd[4], h[4];
    int free = 0;
    bool start = true;
    drmp3dec keep;
    seek_to(&in, 0);
    for (;;) {
        if (have - at < 16384) {
            memmove(buf.data(), buf.data() + at, have - at);
            off += at;
            have -= at;
            at = 0;
            have += in_read(&in, buf.data() + have, buf.size() - have);
        }
        if (at == have) {
            break;
        }
        if (start) {
            keep = dec;
        }
        int k = drmp3dec_decode_frame(&dec, buf.data() + at, (int)(have - at), NULL, &fi);
        if (!fi.frame_bytes) {
            break;
        }
        if (start && fi.layer == 3) {
            // only from the start, after junk ffmpeg still has the old reservoir and minimp3 has none
            int n = drmp3_hdr_frame_bytes(dec.header, dec.free_format_bytes) + drmp3_hdr_padding(dec.header);
            uint8_t *fr = buf.data() + at + fi.frame_bytes - std::min(n, fi.frame_bytes);
            start = !k && n <= fi.frame_bytes && (in.fix.empty() || fi.frame_bytes == n) && mp3_fix(fr, n, in.fix.empty() ? 0 : keep.reserv);
            if (start) {
                if (in.fix.empty()) {
                    in.fix_at = off + at + (uint64_t)(fi.frame_bytes - n);
                }
                in.fix.append((char *)fr, (size_t)n);
                dec = keep;
                k = drmp3dec_decode_frame(&dec, buf.data() + at, (int)(have - at), NULL, &fi);
            }
        }
        at += (size_t)fi.frame_bytes;
        if (k) {
            last = off + at;
            memcpy(hd, dec.header, 4);
            free = dec.free_format_bytes;
        }
    }
    if (!last || !seek_to(&in, last - in.base) || in_read(&in, h, 4) < 4 || !drmp3_hdr_compare(hd, h)) {
        return;
    }
    uint64_t end = last + (uint64_t)(drmp3_hdr_frame_bytes(h, free) + drmp3_hdr_padding(h));
    if (end > in.len) {
        // ffmpeg reads zeros past the cut and stops the huffman data there, all ones decodes to zeros in nearly every table
        std::vector<uint8_t> fr(end - last);
        size_t got = in_read(&in, fr.data() + 4, in.len - last - 4) + 4;
        memcpy(fr.data(), h, 4);
        bool v1 = h[1] & 8;
        size_t nch = (h[3] >> 6) == 3 ? 1 : 2;
        size_t p = (4 + (h[1] & 1 ? 0 : 2)) * 8, side = p / 8 + (v1 ? (nch == 1 ? 17 : 32) : (nch == 1 ? 9 : 17));
        auto get = [&](int n) {
            int v = 0;
            for (; n--; p++) {
                v = v << 1 | (fr[p / 8] >> (7 - p % 8) & 1);
            }
            return v;
        };
        int64_t cut = ((int64_t)got - (int64_t)side) * 8, zero = 0;
        int64_t edge = got >= side ? -8 * get(v1 ? 9 : 8) : 0;
        p += v1 ? (nch == 1 ? 5 : 3) + 4 * nch : nch;
        for (size_t g = 0; got >= side && g < (v1 ? 2 : 1) * nch; g++) {
            int len = get(12);
            p += (v1 ? 59 : 63) - 12;
            if (edge < 0 && edge < cut && edge + len > cut) {
                zero = edge + len;
            }
            edge += len;
        }
        for (size_t i = got; i < fr.size(); i++) {
            in.tail += i < side || ((int64_t)i - (int64_t)side) * 8 < zero ? '\0' : '\xff';
        }
    }
    in.len = std::min(in.len, end);
}

struct Mp3 : Dec {
    drmp3 m;
    bool ok = false, eof = false, table = false;
    size_t skip = 0, cut = 0, spf = 0;
    uint64_t count = 0;
    // decoded floats from frame start on, pos is the next frame to hand out. frames count from the first after the Info frame
    std::vector<float> buf;
    uint64_t start = 0, pos = 0;
    std::vector<drmp3_seek_point> points;

    ~Mp3() {
        if (ok) {
            drmp3_uninit(&m);
        }
    }

    void init() {
        uint64_t full = in.len;
        in.len = mp3_strip(in);
        in.base = mp3_tag(in, full, skip, cut, count);
        mp3_end(in);
        if (!seek_to(&in, 0) || !drmp3_init(&m, in_read, dr_seek<drmp3_seek_origin>, dr_tell, NULL, &in, NULL)) {
            fail();
        }
        ok = true;
        rate = (int)m.sampleRate;
        channels = m.channels;
        width = 2;
        spf = rate >= 32000 ? 1152 : 576;
        uint64_t n = drmp3_get_pcm_frame_count(&m);
        if (!n) {
            fail();
        }
        // ffmpeg only drops the padding where the tag says the file ends
        if (cut) {
            uint64_t end = count * spf;
            cut = n > end || end < cut ? 0 : n + cut > end ? n + cut - end : 0;
        }
        frames = n > skip + cut ? n - skip - cut : 0;
        exact = true;
        pos = skip;
    }

    // j and to are dr_mp3 frames. end is where the padding starts, if known
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
            uint64_t lim = eof ? end : end / spf * spf;
            if (pos < lim) {
                size_t k = (size_t)std::min<uint64_t>(n - done, lim - pos);
                convert(out + done * c * 2, pos, pos + k, eof ? end : UINT64_MAX);
                pos += k;
                done += k;
                continue;
            }
            if (eof) {
                break;
            }
            size_t drop = (size_t)std::min<uint64_t>(pos - start, buf.size() / c);
            buf.erase(buf.begin(), buf.begin() + (ptrdiff_t)(drop * c));
            start += drop;
            size_t old = buf.size();
            buf.resize(old + 16384);
            size_t k = drmp3_read_pcm_frames_f32(&m, 16384 / c, &buf[old]);
            buf.resize(old + k * c);
            eof = k == 0;
        }
        return done * c * 2;
    }

    void seek(uint64_t to) {
        to = std::min(to, frames);
        if (!table) {
            drmp3_uint32 k = 4096;
            points.resize(k);
            if (drmp3_calculate_seek_points(&m, &k, points.data())) {
                points.resize(k);
                drmp3_bind_seek_table(&m, k, points.data());
            }
            table = true;
        }
        drmp3_seek_to_pcm_frame(&m, skip + to);
        buf.clear();
        start = pos = skip + to;
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
    if (pk.empty() || head[0].size() < 30 || head[2].empty()) {
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
    int64_t total = 0, skip = (int64_t)(0 - (uint64_t)pk.back().gran);
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
                skip = (int64_t)((uint64_t)x.gran - (uint64_t)pk.back().gran);
            }
        }
    }
    return skip > 0 && skip <= d ? total - skip : total;
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
        while (!v) {
            if (!more()) {
                fail();
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
    } else if (len >= 4 && (!memcmp(h, "ID3", 3) || (h[0] == 0xff && (h[1] & 0xe0) == 0xe0))) {
        d.reset(new Mp3);
    } else {
        if (in.f) {
            fclose(in.f);
        }
        fail();
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
