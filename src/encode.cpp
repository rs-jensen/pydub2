#include <algorithm>
#include <cstring>
#include <exception>
#include <new>
#include <stdexcept>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#else
#include <sys/mman.h>
#endif

#include <FLAC/stream_encoder.h>
#include <vorbis/vorbisenc.h>
#include <opusenc.h>

#include "core.h"

namespace pydub2 {

static int32_t sample(const Audio &a, size_t i) {
    const char *p = a.data.data() + i * a.width;
    if (a.width == 1) {
        return (int8_t)*p;
    }
    if (a.width == 2) {
        int16_t s;
        memcpy(&s, p, 2);
        return s;
    }
    if (a.width == 3) {
        return (uint8_t)p[0] | (uint8_t)p[1] << 8 | (int8_t)p[2] * 65536;
    }
    int32_t s;
    memcpy(&s, p, 4);
    return s;
}

// frames first to first + n as floats in wav order, a piece at a time so the whole file is never held as floats
static void floats(const Audio &a, size_t first, size_t n, std::vector<float> &out) {
    float scale = 1.0f / (float)(1u << (a.width * 8 - 1));
    size_t from = first * a.channels;
    out.resize(n * a.channels);
    for (size_t i = 0; i < out.size(); i++) {
        out[i] = (float)sample(a, from + i) * scale;
    }
}

// the wav order of 3 to 8 channels is not the order vorbis and opus expect
static void vorbis_order(std::vector<float> &pcm, size_t c) {
    if (c < 3 || c > 8) {
        return;
    }
    float f[8];
    for (size_t i = 0; i < pcm.size() / c; i++) {
        for (size_t j = 0; j < c; j++) {
            f[order[c - 1][j]] = pcm[i * c + j];
        }
        memcpy(&pcm[i * c], f, c * sizeof(float));
    }
}

static void put32(std::string &s, size_t v) {
    for (int i = 0; i < 4; i++) {
        s += (char)(v >> (8 * i));
    }
}

static void put16(std::string &s, size_t v) {
    s += (char)v;
    s += (char)(v >> 8);
}

// same bytes as the wave module writes for pydub
std::string wav_head(const Audio &a) {
    if (a.data.size() > UINT32_MAX - 36) {
        throw std::runtime_error("too long for wav");
    }
    uint32_t n = (uint32_t)a.data.size();
    std::string out = "RIFF";
    put32(out, 36 + n + (n & 1));
    out += "WAVEfmt ";
    put32(out, 16);
    put16(out, 1);
    put16(out, a.channels);
    put32(out, (size_t)a.rate);
    uint64_t byte_rate = (uint64_t)a.rate * a.channels * a.width;
    if (byte_rate > UINT32_MAX) {
        throw std::runtime_error("cant encode this");
    }
    put32(out, byte_rate);
    put16(out, a.channels * a.width);
    put16(out, a.width * 8);
    out += "data";
    put32(out, n);
    return out;
}

static void wav(const Audio &a, const Alloc &alloc) {
    uint32_t n = (uint32_t)a.data.size();
    std::string out = wav_head(a);
    size_t start = out.size();
    char *p = alloc(start + n + (n & 1));
    memcpy(p, out.data(), start);
    memcpy(p + start, a.data.data(), n);
    // 8 bit wav is unsigned
    if (a.width == 1) {
        for (size_t i = start; i < start + n; i++) {
            p[i] = (char)(p[i] + 128);
        }
    }
    if (n & 1) {
        p[start + n] = 0;
    }
}

// pages straight from the system, malloc can keep a freed block and the peak would not go down as they are copied out
static char *map(size_t n) {
#ifdef _WIN32
    void *p = VirtualAlloc(nullptr, n, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!p) {
        throw std::bad_alloc();
    }
#else
    void *p = mmap(nullptr, n, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) {
        throw std::bad_alloc();
    }
#endif
    return (char *)p;
}

static void unmap(char *p, size_t n) {
#ifdef _WIN32
    (void)n;
    VirtualFree(p, 0, MEM_RELEASE);
#else
    munmap(p, n);
#endif
}

// the encoded file in blocks, a string that grows is copied over and over and then once more into the bytes python gets
struct Sink {
    static constexpr size_t block = 1 << 20;
    std::vector<char *> blocks;
    size_t pos = 0, size = 0;

    Sink() = default;
    Sink(const Sink &) = delete;
    ~Sink() {
        for (char *b : blocks) {
            if (b) {
                unmap(b, block);
            }
        }
    }

    void write(const char *buf, size_t len) {
        while (blocks.size() * block < pos + len) {
            blocks.reserve(blocks.size() + 1);
            blocks.push_back(map(block));
        }
        for (size_t done = 0; done < len;) {
            size_t at = (pos + done) % block, n = std::min(len - done, block - at);
            memcpy(blocks[(pos + done) / block] + at, buf + done, n);
            done += n;
        }
        pos += len;
        size = std::max(size, pos);
    }

    // bytes from to to, each block is let go once it has been copied
    char *drain(char *out, size_t from, size_t to) {
        while (from < to) {
            size_t at = from % block, n = std::min(to - from, block - at);
            char *&b = blocks[from / block];
            memcpy(out, b + at, n);
            out += n;
            from += n;
            if (from % block == 0) {
                unmap(b, block);
                b = nullptr;
            }
        }
        return out;
    }
};

static FLAC__StreamEncoderWriteStatus flac_write(const FLAC__StreamEncoder *, const FLAC__byte buf[], size_t len,
                                                 uint32_t, uint32_t, void *user) {
    try {
        ((Sink *)user)->write((const char *)buf, len);
    } catch (...) {
        return FLAC__STREAM_ENCODER_WRITE_STATUS_FATAL_ERROR;
    }
    return FLAC__STREAM_ENCODER_WRITE_STATUS_OK;
}

static FLAC__StreamEncoderSeekStatus flac_seek(const FLAC__StreamEncoder *, FLAC__uint64 pos, void *user) {
    ((Sink *)user)->pos = pos;
    return FLAC__STREAM_ENCODER_SEEK_STATUS_OK;
}

static FLAC__StreamEncoderTellStatus flac_tell(const FLAC__StreamEncoder *, FLAC__uint64 *pos, void *user) {
    *pos = ((Sink *)user)->pos;
    return FLAC__STREAM_ENCODER_TELL_STATUS_OK;
}

static void flac(const Audio &a, const Tags &tags, const Cancel &cancel, const Alloc &alloc) {
    if (a.channels > 8) {
        throw std::runtime_error("cant encode this");
    }
    size_t frames = a.data.size() / a.width / a.channels;
    // ffmpeg writes 8 bit as 16 and 32 bit as 24, so do we
    uint32_t bits = a.width == 1 ? 16 : a.width == 4 ? 24 : (uint32_t)a.width * 8;
    // its block size is the biggest standard one under 105 ms
    uint32_t sizes[] = {16, 192, 256, 512, 576, 1024, 1152, 2048, 2304, 4096, 4608, 8192, 16384};
    uint32_t block = sizes[0];
    for (uint32_t b : sizes) {
        if (b <= (uint64_t)a.rate * 105 / 1000) {
            block = b;
        }
    }
    // a file of the samples as they are, a header on each block and the stream info, and the comment block that is put in
    std::string comment;
    if (!tags.empty()) {
        // not through libFLAC, its metadata calls drop keys and values ffmpeg writes as they are
        std::string c;
        put32(c, strlen(FLAC__VENDOR_STRING));
        c += FLAC__VENDOR_STRING;
        put32(c, tags.size());
        for (auto &t : tags) {
            put32(c, t.first.size() + 1 + t.second.size());
            c += t.first + "=" + t.second;
        }
        if (c.size() >= 1 << 24) {
            throw std::runtime_error("cant encode this");
        }
        comment = "\x84";
        comment += (char)(c.size() >> 16);
        comment += (char)(c.size() >> 8);
        comment += (char)c.size();
        comment += c;
    }
    std::vector<FLAC__int32> buf(block * a.channels);
    FLAC__StreamEncoder *enc = FLAC__stream_encoder_new();
    FLAC__stream_encoder_set_channels(enc, (uint32_t)a.channels);
    FLAC__stream_encoder_set_bits_per_sample(enc, bits);
    FLAC__stream_encoder_set_blocksize(enc, block);
    FLAC__stream_encoder_set_sample_rate(enc, (uint32_t)a.rate);
    FLAC__stream_encoder_set_total_samples_estimate(enc, frames);
    Sink s;
    bool ok = FLAC__stream_encoder_init_stream(enc, flac_write, flac_seek, flac_tell, nullptr, &s) ==
              FLAC__STREAM_ENCODER_INIT_STATUS_OK;
    try {
        for (size_t f = 0; ok && f < frames; f += block) {
            cancel();
            size_t n = std::min(frames - f, (size_t)block);
            for (size_t i = 0; i < n * a.channels; i++) {
                int32_t v = sample(a, f * a.channels + i);
                buf[i] = a.width == 1 ? v * 256 : a.width == 4 ? v >> 8 : v;
            }
            ok = FLAC__stream_encoder_process_interleaved(enc, buf.data(), (uint32_t)n);
        }
    } catch (...) {
        FLAC__stream_encoder_delete(enc);
        throw;
    }
    ok = FLAC__stream_encoder_finish(enc) && ok;
    FLAC__stream_encoder_delete(enc);
    if (!ok) {
        throw std::runtime_error("cant encode this");
    }
    size_t at = s.size, old = 0;
    if (!tags.empty()) {
        // libflac wrote its own comment block after the stream info, ours takes its place
        if (s.size < 46) {
            throw std::runtime_error("cant encode this");
        }
        const char *b = s.blocks[0];
        at = 42;
        old = 4 + ((uint8_t)b[43] << 16 | (uint8_t)b[44] << 8 | (uint8_t)b[45]);
        if (s.size < at + old) {
            throw std::runtime_error("cant encode this");
        }
    }
    char *out = alloc(s.size - old + comment.size());
    out = s.drain(out, 0, at);
    memcpy(out, comment.data(), comment.size());
    s.drain(out + comment.size(), at + old, s.size);
}

static void pages(ogg_stream_state *os, std::string &out, bool flush) {
    ogg_page og;
    while (flush ? ogg_stream_flush(os, &og) : ogg_stream_pageout(os, &og)) {
        out.append((char *)og.header, (size_t)og.header_len);
        out.append((char *)og.body, (size_t)og.body_len);
    }
}

static void blocks(vorbis_dsp_state *vd, vorbis_block *vb, ogg_stream_state *os, std::string &out) {
    ogg_packet op;
    while (vorbis_analysis_blockout(vd, vb) == 1) {
        vorbis_analysis(vb, nullptr);
        vorbis_bitrate_addblock(vb);
        while (vorbis_bitrate_flushpacket(vd, &op)) {
            ogg_stream_packetin(os, &op);
            pages(os, out, false);
        }
    }
}

static std::string ogg(const Audio &a, int bitrate, const Tags &tags, const Cancel &cancel) {
    vorbis_info vi;
    vorbis_info_init(&vi);
    // q 3 like ffmpeg when no bitrate is given, and with a bitrate ffmpeg turns the slow rate management off
    long ch = (long)a.channels;
    int err = bitrate ? vorbis_encode_setup_managed(&vi, ch, a.rate, -1, bitrate, -1)
                      : vorbis_encode_setup_vbr(&vi, ch, a.rate, 0.3f);
    if (!err && bitrate) {
        err = vorbis_encode_ctl(&vi, OV_ECTL_RATEMANAGE2_SET, nullptr);
    }
    if (!err) {
        err = vorbis_encode_setup_init(&vi);
    }
    if (err) {
        vorbis_info_clear(&vi);
        throw std::runtime_error("cant encode this");
    }
    vorbis_comment vc;
    vorbis_comment_init(&vc);
    for (auto &t : tags) {
        vorbis_comment_add(&vc, (t.first + "=" + t.second).c_str());
    }
    vorbis_dsp_state vd;
    vorbis_analysis_init(&vd, &vi);
    vorbis_block vb;
    vorbis_block_init(&vd, &vb);
    ogg_stream_state os;
    ogg_stream_init(&os, 1);

    std::string out;
    std::exception_ptr ex;
    try {
        ogg_packet h1, h2, h3;
        vorbis_analysis_headerout(&vd, &vc, &h1, &h2, &h3);
        ogg_stream_packetin(&os, &h1);
        ogg_stream_packetin(&os, &h2);
        ogg_stream_packetin(&os, &h3);
        pages(&os, out, true);

        size_t frames = a.data.size() / a.width / a.channels;
        std::vector<float> pcm;
        for (size_t f = 0; f < frames; f += 4096) {
            cancel();
            size_t n = std::min(frames - f, (size_t)4096);
            floats(a, f, n, pcm);
            vorbis_order(pcm, a.channels);
            float **buf = vorbis_analysis_buffer(&vd, (int)n);
            for (size_t i = 0; i < n; i++) {
                for (size_t c = 0; c < a.channels; c++) {
                    buf[c][i] = pcm[i * a.channels + c];
                }
            }
            vorbis_analysis_wrote(&vd, (int)n);
            blocks(&vd, &vb, &os, out);
        }
        vorbis_analysis_wrote(&vd, 0);
        blocks(&vd, &vb, &os, out);
        pages(&os, out, true);
    } catch (...) {
        ex = std::current_exception();
    }

    ogg_stream_clear(&os);
    vorbis_block_clear(&vb);
    vorbis_dsp_clear(&vd);
    vorbis_comment_clear(&vc);
    vorbis_info_clear(&vi);
    if (ex) {
        std::rethrow_exception(ex);
    }
    return out;
}

static int opus_write(void *user, const unsigned char *p, opus_int32 len) {
    try {
        ((std::string *)user)->append((const char *)p, (size_t)len);
    } catch (...) {
        return 1;
    }
    return 0;
}

static int opus_close(void *) {
    return 0;
}

static std::string opus(const Audio &a, int bitrate, const Tags &tags, const Cancel &cancel) {
    std::string out;
    OpusEncCallbacks cb = {opus_write, opus_close};
    OggOpusComments *com = ope_comments_create();
    for (auto &t : tags) {
        ope_comments_add(com, t.first.c_str(), t.second.c_str());
    }
    int err = 0;
    OggOpusEnc *enc = ope_encoder_create_callbacks(&cb, &out, com, a.rate, (int)a.channels, a.channels > 2, &err);
    ope_comments_destroy(com);
    if (!enc) {
        throw std::runtime_error("cant encode this");
    }
    // ffmpeg: 64k per stream plus 32k per coupled pair, plain vbr, no padding in the tags
    int streams[] = {1, 1, 2, 2, 3, 4, 5, 5}, pairs[] = {0, 1, 1, 2, 2, 2, 2, 3};
    if (!bitrate && a.channels <= 8) {
        bitrate = 64000 * streams[a.channels - 1] + 32000 * pairs[a.channels - 1];
    }
    if (bitrate) {
        err = ope_encoder_ctl(enc, OPUS_SET_BITRATE(bitrate));
    }
    err = err || ope_encoder_ctl(enc, OPUS_SET_VBR_CONSTRAINT(0));
    err = err || ope_encoder_ctl(enc, OPE_SET_COMMENT_PADDING(0));
    // ffmpeg encodes at the next rate opus has, which caps the bandwidth
    int band = a.rate <= 8000 ? OPUS_BANDWIDTH_NARROWBAND : a.rate <= 12000 ? OPUS_BANDWIDTH_MEDIUMBAND
             : a.rate <= 16000 ? OPUS_BANDWIDTH_WIDEBAND : a.rate <= 24000 ? OPUS_BANDWIDTH_SUPERWIDEBAND
                                                                            : OPUS_BANDWIDTH_FULLBAND;
    err = err || ope_encoder_ctl(enc, OPUS_SET_MAX_BANDWIDTH(band));
    // it takes the frame count as an int
    size_t frames = a.data.size() / a.width / a.channels, step = 1 << 16;
    try {
        std::vector<float> pcm;
        for (size_t f = 0; !err && f < frames; f += step) {
            cancel();
            size_t n = std::min(frames - f, step);
            floats(a, f, n, pcm);
            vorbis_order(pcm, a.channels);
            err = ope_encoder_write_float(enc, pcm.data(), (int)n);
        }
    } catch (...) {
        ope_encoder_destroy(enc);
        throw;
    }
    if (!err) {
        err = ope_encoder_drain(enc);
    }
    ope_encoder_destroy(enc);
    if (err) {
        throw std::runtime_error("cant encode this");
    }
    return out;
}

static void copy(const std::string &s, const Alloc &alloc) {
    memcpy(alloc(s.size()), s.data(), s.size());
}

void encode(const Audio &a, const std::string &format, int bitrate, const Tags &tags, const Cancel &cancel,
            const Alloc &alloc) {
    if (a.width < 1 || a.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    if (format == "wav") {
        return wav(a, alloc);
    }
    if (format == "flac") {
        return flac(a, tags, cancel, alloc);
    }
    if (format == "ogg") {
        return copy(ogg(a, bitrate, tags, cancel), alloc);
    }
    if (format == "opus") {
        // libopusenc corrupts the heap when it resamples up from below about 90 hz, and down from 1 GHz takes minutes
        if (a.rate < 100 || a.rate > 768000) {
            throw std::runtime_error("cant encode this");
        }
        return copy(opus(a, bitrate, tags, cancel), alloc);
    }
    throw std::invalid_argument("cant write this format");
}

}
