#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "core.h"
#include "silero_weights.h"

namespace pydub2 {

// the weights flipped so the output index is last and the loops over outputs run on neighbours in memory
static std::vector<float> flip(const float *w, size_t rows, size_t cols) {
    std::vector<float> t(rows * cols);
    for (size_t r = 0; r < rows; r++) {
        for (size_t c = 0; c < cols; c++) {
            t[c * rows + r] = w[r * cols + c];
        }
    }
    return t;
}

struct Net {
    std::vector<float> basis; // [256][258], the cos part then the sin part
    std::vector<float> enc0, enc1, enc2, enc3; // [cin * 3][cout]
    std::vector<float> ih, hh; // [128][512]
};

static Net load() {
    Net n;
    // silero's stft weights are just a periodic hann times cos and sin, so build them instead of shipping them
    double pi = std::acos(-1.0);
    n.basis.resize(256 * 258);
    for (unsigned k = 0; k < 129; k++) {
        for (unsigned i = 0; i < 256; i++) {
            double win = 0.5 - 0.5 * std::cos(2 * pi * i / 256);
            double a = 2 * pi * ((k * i) % 256) / 256;
            n.basis[i * 258 + k] = (float)(win * std::cos(a));
            n.basis[i * 258 + 129 + k] = (float)(-win * std::sin(a));
        }
    }
    n.enc0 = flip(enc0_w, 128, 129 * 3);
    n.enc1 = flip(enc1_w, 64, 128 * 3);
    n.enc2 = flip(enc2_w, 64, 64 * 3);
    n.enc3 = flip(enc3_w, 128, 64 * 3);
    n.ih = flip(ih_w, 512, 128);
    n.hh = flip(hh_w, 512, 128);
    return n;
}

static void madd(float *__restrict out, const float *__restrict w, float v, int n) {
    for (int i = 0; i < n; i++) {
        out[i] += w[i] * v;
    }
}

// kernel 3, pad 1, then relu. in is [t][cin], out is [t out][cout]
static int conv(const float *in, int cin, int t, const std::vector<float> &w, const float *b, int cout, int stride,
                float *out) {
    int tout = (t - 1) / stride + 1;
    for (int j = 0; j < tout; j++) {
        std::copy(b, b + cout, out + j * cout);
    }
    for (int i = 0; i < cin; i++) {
        for (int k = 0; k < 3; k++) {
            for (int j = 0; j < tout; j++) {
                int p = j * stride + k - 1;
                if (p >= 0 && p < t) {
                    madd(out + j * cout, w.data() + (i * 3 + k) * cout, in[p * cin + i], cout);
                }
            }
        }
    }
    for (int i = 0; i < tout * cout; i++) {
        out[i] = out[i] > 0 ? out[i] : 0;
    }
    return tout;
}

static void gates(float *__restrict g, const float *__restrict a, float x, const float *__restrict b, float y) {
    for (int r = 0; r < 512; r++) {
        g[r] += a[r] * x + b[r] * y;
    }
}

static float sigmoid(float x) {
    return 1 / (1 + std::exp(-x));
}

static const Net &shared() {
    static const Net net = load();
    return net;
}

// x is fed in 16 khz mono, one probability per 512 samples like silero's get_speech_timestamps
struct Probs {
    const Net &net = shared();
    const Cancel &cancel;
    std::vector<float> out;
    float buf[640] = {0}, h[128] = {0}, c[128] = {0}, win[512] = {0};
    size_t have = 0;

    Probs(const Cancel &cancel) : cancel(cancel) {}

    void feed(const float *x, size_t n) {
        for (size_t i = 0; i < n; i++) {
            win[have++] = x[i];
            if (have == 512) {
                run();
            }
        }
    }

    void finish() {
        if (have) {
            run();
        }
    }

    void run() {
        float acc[4 * 258], mag[4 * 129], e0[4 * 128], e1[2 * 64], e2[64], e3[128], g[512];
        cancel();
        // 64 samples of context from the last window, then reflect pad 64 on the right
        std::memmove(buf, buf + 512, 64 * sizeof(float));
        for (size_t i = 0; i < 512; i++) {
            buf[64 + i] = i < have ? win[i] : 0;
        }
        have = 0;
        for (int i = 0; i < 64; i++) {
            buf[576 + i] = buf[574 - i];
        }

        memset(acc, 0, sizeof(acc));
        for (int n = 0; n < 256; n++) {
            for (int t = 0; t < 4; t++) {
                madd(acc + t * 258, net.basis.data() + n * 258, buf[t * 128 + n], 258);
            }
        }
        for (int t = 0; t < 4; t++) {
            for (int k = 0; k < 129; k++) {
                float re = acc[t * 258 + k], im = acc[t * 258 + 129 + k];
                mag[t * 129 + k] = std::sqrt(re * re + im * im);
            }
        }

        int t = conv(mag, 129, 4, net.enc0, enc0_b, 128, 1, e0);
        t = conv(e0, 128, t, net.enc1, enc1_b, 64, 2, e1);
        t = conv(e1, 64, t, net.enc2, enc2_b, 64, 2, e2);
        conv(e2, 64, t, net.enc3, enc3_b, 128, 1, e3);

        // lstm cell, torch gate order i f g o
        for (int r = 0; r < 512; r++) {
            g[r] = ih_b[r] + hh_b[r];
        }
        for (int i = 0; i < 128; i++) {
            gates(g, net.ih.data() + i * 512, e3[i], net.hh.data() + i * 512, h[i]);
        }
        float o = out_b[0];
        for (int i = 0; i < 128; i++) {
            c[i] = sigmoid(g[128 + i]) * c[i] + sigmoid(g[i]) * std::tanh(g[256 + i]);
            h[i] = sigmoid(g[384 + i]) * std::tanh(c[i]);
            o += out_w[i] * (h[i] > 0 ? h[i] : 0);
        }
        out.push_back(sigmoid(o));
    }
};

Ranges detect_speech(const Audio &in, double threshold, int min_speech_ms, int min_silence_ms, const Cancel &cancel) {
    if (in.width < 1 || in.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    Probs probs(cancel);
    size_t n = 0;
    float scale = (float)(1.0 / ((double)(1u << (8 * in.width - 1)) * (double)in.channels));
    // the mixdown of a piece of 16 khz audio, a few thousand frames at a time
    auto feed = [&](std::string_view pcm) {
        float x[4096];
        const uint8_t *p = (const uint8_t *)pcm.data();
        size_t left = pcm.size() / in.width / in.channels;
        n += left;
        while (left) {
            size_t k = std::min<size_t>(left, 4096);
            for (size_t i = 0; i < k; i++) {
                float sum = 0;
                for (size_t ch = 0; ch < in.channels; ch++, p += in.width) {
                    if (in.width == 1) {
                        sum += (int8_t)p[0];
                    } else if (in.width == 2) {
                        sum += (int16_t)(p[0] | p[1] << 8);
                    } else if (in.width == 3) {
                        sum += (float)((int32_t)((uint32_t)p[0] << 8 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 24) / 256);
                    } else {
                        sum += (float)(int32_t)((uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24);
                    }
                }
                x[i] = sum * scale;
            }
            probs.feed(x, k);
            left -= k;
        }
    };
    if (in.rate != 16000) {
        resample_each(in, 16000, cancel, feed);
    } else {
        feed(in.data);
    }
    probs.finish();

    // get_speech_timestamps from silero with no padding and no max speech length, in samples
    long long min_speech = 16LL * min_speech_ms, min_silence = 16LL * min_silence_ms;
    double neg = threshold - 0.15;
    Ranges out;
    bool on = false;
    long long start = 0, temp_end = 0;
    for (size_t i = 0; i < probs.out.size(); i++) {
        long long pos = 512LL * (long long)i;
        if (probs.out[i] >= threshold) {
            temp_end = 0;
        }
        if (probs.out[i] >= threshold && !on) {
            on = true;
            start = pos;
            continue;
        }
        if (probs.out[i] < neg && on) {
            if (!temp_end) {
                temp_end = pos;
            }
            if (pos - temp_end < min_silence) {
                continue;
            }
            if (temp_end - start > min_speech) {
                out.push_back({(int)(start / 16), (int)(temp_end / 16)});
            }
            on = false;
            temp_end = 0;
        }
    }
    if (on && (long long)n - start > min_speech) {
        out.push_back({(int)(start / 16), (int)(n / 16)});
    }
    return out;
}

}
