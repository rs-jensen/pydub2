#include <algorithm>
#include <cstring>
#include <exception>
#include <stdexcept>

#include <FLAC/stream_encoder.h>
#include <vorbis/vorbisenc.h>
#include <opusenc.h>

#include "core.h"

namespace pydub2 {

static int32_t sample(const Audio &a, size_t i) {
    const char *p = a.data.data() + i * a.width;
    if (a.width == 1)
        return (int8_t)*p;
    if (a.width == 2) {
        int16_t s;
        memcpy(&s, p, 2);
        return s;
    }
    if (a.width == 3)
        return (uint8_t)p[0] | (uint8_t)p[1] << 8 | (int8_t)p[2] * 65536;
    int32_t s;
    memcpy(&s, p, 4);
    return s;
}

static std::vector<float> floats(const Audio &a) {
    size_t n = a.data.size() / a.width;
    float scale = 1.0f / (float)(1u << (a.width * 8 - 1));
    std::vector<float> out(n);
    for (size_t i = 0; i < n; i++)
        out[i] = sample(a, i) * scale;
    return out;
}

static void put32(std::string &s, uint32_t v) {
    for (int i = 0; i < 4; i++)
        s += (char)(v >> (8 * i));
}

static void put16(std::string &s, uint16_t v) {
    s += (char)v;
    s += (char)(v >> 8);
}

// same bytes as the wave module writes for pydub
static std::string wav(const Audio &a) {
    if (a.data.size() > UINT32_MAX - 36)
        throw std::runtime_error("too long for wav");
    uint32_t n = a.data.size();
    std::string out = "RIFF";
    put32(out, 36 + n + (n & 1));
    out += "WAVEfmt ";
    put32(out, 16);
    put16(out, 1);
    put16(out, a.channels);
    put32(out, a.rate);
    put32(out, a.rate * a.channels * a.width);
    put16(out, a.channels * a.width);
    put16(out, a.width * 8);
    out += "data";
    put32(out, n);
    size_t start = out.size();
    out += a.data;
    // 8 bit wav is unsigned
    if (a.width == 1)
        for (size_t i = start; i < out.size(); i++)
            out[i] = (char)(out[i] + 128);
    if (n & 1)
        out += '\0';
    return out;
}

struct Sink {
    std::string out;
    size_t pos = 0;
};

static FLAC__StreamEncoderWriteStatus flac_write(const FLAC__StreamEncoder *, const FLAC__byte buf[], size_t len,
                                                 uint32_t, uint32_t, void *user) {
    Sink *s = (Sink *)user;
    try {
        if (s->pos + len > s->out.size())
            s->out.resize(s->pos + len);
    } catch (...) {
        return FLAC__STREAM_ENCODER_WRITE_STATUS_FATAL_ERROR;
    }
    memcpy(&s->out[s->pos], buf, len);
    s->pos += len;
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

static std::string flac(const Audio &a) {
    size_t frames = a.data.size() / a.width / a.channels;
    // ffmpeg writes 8 bit as 16 and 32 bit as 24, so do we
    int bits = a.width == 1 ? 16 : a.width == 4 ? 24 : a.width * 8;
    // its block size is the biggest standard one under 105 ms
    uint32_t sizes[] = {16, 192, 256, 512, 576, 1024, 1152, 2048, 2304, 4096, 4608, 8192, 16384};
    uint32_t block = sizes[0];
    for (uint32_t b : sizes)
        if (b <= (uint64_t)a.rate * 105 / 1000)
            block = b;
    std::vector<FLAC__int32> buf(block * a.channels);
    FLAC__StreamEncoder *enc = FLAC__stream_encoder_new();
    FLAC__stream_encoder_set_channels(enc, a.channels);
    FLAC__stream_encoder_set_bits_per_sample(enc, bits);
    FLAC__stream_encoder_set_blocksize(enc, block);
    FLAC__stream_encoder_set_sample_rate(enc, a.rate);
    FLAC__stream_encoder_set_total_samples_estimate(enc, frames);
    Sink s;
    bool ok = FLAC__stream_encoder_init_stream(enc, flac_write, flac_seek, flac_tell, nullptr, &s) ==
              FLAC__STREAM_ENCODER_INIT_STATUS_OK;
    for (size_t f = 0; ok && f < frames; f += block) {
        size_t n = std::min(frames - f, (size_t)block);
        for (size_t i = 0; i < n * a.channels; i++) {
            int32_t v = sample(a, f * a.channels + i);
            buf[i] = a.width == 1 ? v * 256 : a.width == 4 ? v >> 8 : v;
        }
        ok = FLAC__stream_encoder_process_interleaved(enc, buf.data(), n);
    }
    ok = FLAC__stream_encoder_finish(enc) && ok;
    FLAC__stream_encoder_delete(enc);
    if (!ok)
        throw std::runtime_error("cant encode this");
    return s.out;
}

static void pages(ogg_stream_state *os, std::string &out, bool flush) {
    ogg_page og;
    while (flush ? ogg_stream_flush(os, &og) : ogg_stream_pageout(os, &og)) {
        out.append((char *)og.header, og.header_len);
        out.append((char *)og.body, og.body_len);
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

static std::string ogg(const Audio &a, int bitrate) {
    std::vector<float> pcm = floats(a);
    vorbis_info vi;
    vorbis_info_init(&vi);
    // q 3 like ffmpeg when no bitrate is given, and with a bitrate ffmpeg turns the slow rate management off
    int err = bitrate ? vorbis_encode_setup_managed(&vi, a.channels, a.rate, -1, bitrate, -1)
                      : vorbis_encode_setup_vbr(&vi, a.channels, a.rate, 0.3f);
    if (!err && bitrate)
        err = vorbis_encode_ctl(&vi, OV_ECTL_RATEMANAGE2_SET, nullptr);
    if (!err)
        err = vorbis_encode_setup_init(&vi);
    if (err) {
        vorbis_info_clear(&vi);
        throw std::runtime_error("cant encode this");
    }
    vorbis_comment vc;
    vorbis_comment_init(&vc);
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

        size_t frames = pcm.size() / a.channels;
        for (size_t f = 0; f < frames; f += 4096) {
            int n = std::min(frames - f, (size_t)4096);
            float **buf = vorbis_analysis_buffer(&vd, n);
            for (int i = 0; i < n; i++)
                for (int c = 0; c < a.channels; c++)
                    buf[c][i] = pcm[(f + i) * a.channels + c];
            vorbis_analysis_wrote(&vd, n);
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
    if (ex)
        std::rethrow_exception(ex);
    return out;
}

static int opus_write(void *user, const unsigned char *p, opus_int32 len) {
    try {
        ((std::string *)user)->append((const char *)p, len);
    } catch (...) {
        return 1;
    }
    return 0;
}

static int opus_close(void *) {
    return 0;
}

static std::string opus(const Audio &a, int bitrate) {
    std::string out;
    std::vector<float> pcm = floats(a);
    OpusEncCallbacks cb = {opus_write, opus_close};
    OggOpusComments *com = ope_comments_create();
    int err = 0;
    OggOpusEnc *enc = ope_encoder_create_callbacks(&cb, &out, com, a.rate, a.channels, a.channels > 2, &err);
    ope_comments_destroy(com);
    if (!enc)
        throw std::runtime_error("cant encode this");
    // ffmpeg: 64k per stream plus 32k per coupled pair, plain vbr, no padding in the tags
    int streams[] = {1, 1, 2, 2, 3, 4, 5, 5}, pairs[] = {0, 1, 1, 2, 2, 2, 2, 3};
    if (!bitrate && a.channels <= 8)
        bitrate = 64000 * streams[a.channels - 1] + 32000 * pairs[a.channels - 1];
    if (bitrate)
        err = ope_encoder_ctl(enc, OPUS_SET_BITRATE(bitrate));
    err = err || ope_encoder_ctl(enc, OPUS_SET_VBR_CONSTRAINT(0));
    err = err || ope_encoder_ctl(enc, OPE_SET_COMMENT_PADDING(0));
    // ffmpeg encodes at the next rate opus has, which caps the bandwidth
    int band = a.rate <= 8000 ? OPUS_BANDWIDTH_NARROWBAND : a.rate <= 12000 ? OPUS_BANDWIDTH_MEDIUMBAND
             : a.rate <= 16000 ? OPUS_BANDWIDTH_WIDEBAND : a.rate <= 24000 ? OPUS_BANDWIDTH_SUPERWIDEBAND
                                                                            : OPUS_BANDWIDTH_FULLBAND;
    err = err || ope_encoder_ctl(enc, OPUS_SET_MAX_BANDWIDTH(band));
    // it takes the frame count as an int
    size_t frames = pcm.size() / a.channels, step = 1 << 20;
    for (size_t f = 0; !err && f < frames; f += step)
        err = ope_encoder_write_float(enc, pcm.data() + f * a.channels, std::min(frames - f, step));
    if (!err)
        err = ope_encoder_drain(enc);
    ope_encoder_destroy(enc);
    if (err)
        throw std::runtime_error("cant encode this");
    return out;
}

std::string encode(const Audio &a, const std::string &format, int bitrate) {
    if (a.width < 1 || a.width > 4)
        throw std::invalid_argument("bad sample width");
    if (format == "wav")
        return wav(a);
    if (format == "flac")
        return flac(a);
    if (format == "ogg")
        return ogg(a, bitrate);
    if (format == "opus")
        return opus(a, bitrate);
    throw std::invalid_argument("cant write this format");
}

}
