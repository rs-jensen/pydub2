#include <algorithm>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

#include <samplerate.h>

#include "core.h"

namespace pydub2 {

void resample(const Audio &a, int new_rate, const Cancel &cancel, const Alloc &alloc) {
    if (a.width < 1 || a.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    if (new_rate == a.rate) {
        memcpy(alloc(a.data.size()), a.data.data(), a.data.size());
        return;
    }

    // libsamplerate only takes ratios up to 256 and would say so after the buffers are made
    if (new_rate > 256LL * a.rate || a.rate > 256LL * new_rate) {
        throw std::runtime_error("rates too far apart");
    }
    size_t w = a.width;
    int ch = (int)a.channels;
    long long full = 1LL << (8 * w - 1); // 128, 32768 or 2^31
    long long n = (long long)(a.data.size() / (w * a.channels)); // frames
    long long out_n = std::llrint((double)n * new_rate / a.rate); // half to even, like python round

    if (n > INT32_MAX || out_n > INT32_MAX) {
        throw std::runtime_error("too long to resample");
    }
    std::vector<float> in((size_t)(n * ch));
    for (size_t i = 0; i < in.size(); i++) {
        long long v = 0;
        for (size_t k = w; k--;) {
            v = v * 256 + (unsigned char)a.data[i * w + k];
        }
        if (v >= full) {
            v -= 2 * full;
        }
        in[i] = (float)v / (float)full;
    }

    // zeros stay in the buffer when libsamplerate makes fewer frames than out_n
    std::vector<float> out((size_t)(out_n * ch), 0.0f);
    int err;
    SRC_STATE *st = src_new(SRC_SINC_MEDIUM_QUALITY, ch, &err);
    if (!st) {
        throw std::runtime_error(src_strerror(err));
    }
    SRC_DATA d = {};
    d.src_ratio = (double)new_rate / a.rate;
    long long used = 0, made = 0, step = 1 << 20;
    try {
        do {
            cancel();
            d.data_in = in.data() + used * ch;
            d.input_frames = std::min(step, n - used);
            d.end_of_input = used + d.input_frames == n;
            d.data_out = out.data() + made * ch;
            d.output_frames = out_n - made;
            err = src_process(st, &d);
            used += d.input_frames_used;
            made += d.output_frames_gen;
        } while (!err && !d.end_of_input && made < out_n);
    } catch (...) {
        src_delete(st);
        throw;
    }
    src_delete(st);
    if (err) {
        throw std::runtime_error(src_strerror(err));
    }

    char *res = alloc(out.size() * w);
    long long lo = -full, hi = full - 1;
    for (size_t i = 0; i < out.size(); i++) {
        long long v = std::llround(out[i] * (float)full);
        v = std::min(hi, std::max(lo, v)); // clip, never wrap
        uint64_t u = (uint64_t)v;
        for (size_t k = 0; k < w; k++) {
            res[i * w + k] = (char)(u >> (8 * k));
        }
    }
}

}
