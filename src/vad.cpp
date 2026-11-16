#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

#include "core.h"
#include "silero_weights.h"

namespace pydub2 {

// kernel 3, pad 1, then relu. in is [cin][t], returns [cout][t out]
static std::vector<float> conv(const std::vector<float> &in, int cin, int t, const float *w, const float *b,
                               int cout, int stride, int &tout) {
    tout = (t - 1) / stride + 1;
    std::vector<float> out(cout * tout);
    for (int o = 0; o < cout; o++) {
        for (int j = 0; j < tout; j++) {
            float sum = b[o];
            for (int i = 0; i < cin; i++) {
                for (int k = 0; k < 3; k++) {
                    int p = j * stride + k - 1;
                    if (p >= 0 && p < t)
                        sum += w[(o * cin + i) * 3 + k] * in[i * t + p];
                }
            }
            out[o * tout + j] = sum > 0 ? sum : 0;
        }
    }
    return out;
}

static float sigmoid(float x) {
    return 1 / (1 + std::exp(-x));
}

// x is 16 khz mono, one probability per 512 samples like silero's get_speech_timestamps
static std::vector<float> speech_probs(const std::vector<float> &x, const Cancel &cancel) {
    // silero's stft weights are just a periodic hann times cos and sin, so build them instead of shipping them
    double pi = std::acos(-1.0);
    std::vector<float> basis(258 * 256);
    for (int k = 0; k < 129; k++) {
        for (int n = 0; n < 256; n++) {
            double win = 0.5 - 0.5 * std::cos(2 * pi * n / 256);
            double a = 2 * pi * ((k * n) % 256) / 256;
            basis[k * 256 + n] = (float)(win * std::cos(a));
            basis[(129 + k) * 256 + n] = (float)(-win * std::sin(a));
        }
    }

    std::vector<float> probs;
    float buf[640] = {0};
    float h[128] = {0}, c[128] = {0};
    for (size_t start = 0; start < x.size(); start += 512) {
        cancel();
        // 64 samples of context from the last window, then reflect pad 64 on the right
        std::memmove(buf, buf + 512, 64 * sizeof(float));
        for (int i = 0; i < 512; i++)
            buf[64 + i] = start + i < x.size() ? x[start + i] : 0;
        for (int i = 0; i < 64; i++)
            buf[576 + i] = buf[574 - i];

        std::vector<float> mag(129 * 4);
        for (int k = 0; k < 129; k++) {
            for (int t = 0; t < 4; t++) {
                float re = 0, im = 0;
                for (int n = 0; n < 256; n++) {
                    re += basis[k * 256 + n] * buf[t * 128 + n];
                    im += basis[(129 + k) * 256 + n] * buf[t * 128 + n];
                }
                mag[k * 4 + t] = std::sqrt(re * re + im * im);
            }
        }

        int t;
        std::vector<float> e = conv(mag, 129, 4, enc0_w, enc0_b, 128, 1, t);
        e = conv(e, 128, t, enc1_w, enc1_b, 64, 2, t);
        e = conv(e, 64, t, enc2_w, enc2_b, 64, 2, t);
        e = conv(e, 64, t, enc3_w, enc3_b, 128, 1, t);

        // lstm cell, torch gate order i f g o
        float g[512];
        for (int r = 0; r < 512; r++) {
            float sum = ih_b[r] + hh_b[r];
            for (int i = 0; i < 128; i++)
                sum += ih_w[r * 128 + i] * e[i] + hh_w[r * 128 + i] * h[i];
            g[r] = sum;
        }
        float out = out_b[0];
        for (int i = 0; i < 128; i++) {
            c[i] = sigmoid(g[128 + i]) * c[i] + sigmoid(g[i]) * std::tanh(g[256 + i]);
            h[i] = sigmoid(g[384 + i]) * std::tanh(c[i]);
            out += out_w[i] * (h[i] > 0 ? h[i] : 0);
        }
        probs.push_back(sigmoid(out));
    }
    return probs;
}

Ranges detect_speech(const Audio &in, double threshold, int min_speech_ms, int min_silence_ms, const Cancel &cancel) {
    if (in.width < 1 || in.width > 4)
        throw std::invalid_argument("bad sample width");
    Audio a = in;
    std::string res;
    if (in.rate != 16000) {
        resample(in, 16000, cancel, [&res](size_t n) {
            res.resize(n);
            return &res[0];
        });
        a.data = res;
        a.rate = 16000;
    }
    size_t n = a.data.size() / a.width / a.channels;
    std::vector<float> x(n);
    const uint8_t *p = (const uint8_t *)a.data.data();
    float scale = 1.0f / ((double)(1u << (8 * a.width - 1)) * a.channels);
    for (size_t i = 0; i < n; i++) {
        float sum = 0;
        for (int ch = 0; ch < a.channels; ch++, p += a.width) {
            if (a.width == 1) sum += (int8_t)p[0];
            else if (a.width == 2) sum += (int16_t)(p[0] | p[1] << 8);
            else if (a.width == 3) sum += (int32_t)((uint32_t)p[0] << 8 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 24) / 256;
            else sum += (int32_t)((uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24);
        }
        x[i] = sum * scale;
    }

    std::vector<float> probs = speech_probs(x, cancel);

    // get_speech_timestamps from silero with no padding and no max speech length, in samples
    long long min_speech = 16LL * min_speech_ms, min_silence = 16LL * min_silence_ms;
    double neg = threshold - 0.15;
    Ranges out;
    bool on = false;
    long long start = 0, temp_end = 0;
    for (size_t i = 0; i < probs.size(); i++) {
        long long pos = 512LL * i;
        if (probs[i] >= threshold)
            temp_end = 0;
        if (probs[i] >= threshold && !on) {
            on = true;
            start = pos;
            continue;
        }
        if (probs[i] < neg && on) {
            if (!temp_end)
                temp_end = pos;
            if (pos - temp_end < min_silence)
                continue;
            if (temp_end - start > min_speech)
                out.push_back({(int)(start / 16), (int)(temp_end / 16)});
            on = false;
            temp_end = 0;
        }
    }
    if (on && (long long)n - start > min_speech)
        out.push_back({(int)(start / 16), (int)(n / 16)});
    return out;
}

}
