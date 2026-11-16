#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

#include <samplerate.h>

#include "core.h"

namespace pydub2 {

Audio resample(const Audio &a, int new_rate) {
    if (a.width < 1 || a.width > 4) throw std::invalid_argument("bad sample width");
    if (new_rate == a.rate) return a;

    // libsamplerate only takes ratios up to 256 and would say so after the buffers are made
    if (new_rate > 256LL * a.rate || a.rate > 256LL * new_rate) throw std::runtime_error("rates too far apart");
    int w = a.width;
    long long full = 1LL << (8 * w - 1); // 128, 32768 or 2^31
    long long n = a.data.size() / (w * a.channels); // frames
    long long out_n = std::llrint(n * (double)new_rate / a.rate); // half to even, like python round

    if (n > INT32_MAX || out_n > INT32_MAX) throw std::runtime_error("too long to resample");
    std::vector<float> in(n * a.channels);
    for (size_t i = 0; i < in.size(); i++) {
        long long v = 0;
        for (int k = w - 1; k >= 0; k--) v = v * 256 + (unsigned char)a.data[i * w + k];
        if (v >= full) v -= 2 * full;
        in[i] = (float)v / full;
    }

    // zeros stay in the buffer when libsamplerate makes fewer frames than out_n
    std::vector<float> out(out_n * a.channels, 0.0f);
    SRC_DATA d = {};
    d.data_in = in.data();
    d.input_frames = n;
    d.data_out = out.data();
    d.output_frames = out_n;
    d.src_ratio = (double)new_rate / a.rate;
    int err = src_simple(&d, SRC_SINC_MEDIUM_QUALITY, a.channels);
    if (err) throw std::runtime_error(src_strerror(err));

    Audio r;
    r.rate = new_rate;
    r.channels = a.channels;
    r.width = w;
    r.data.resize(out.size() * w);
    long long lo = -full, hi = full - 1;
    for (size_t i = 0; i < out.size(); i++) {
        long long v = std::llround(out[i] * full);
        v = std::min(hi, std::max(lo, v)); // clip, never wrap
        unsigned long long u = v;
        for (int k = 0; k < w; k++) r.data[i * w + k] = (char)(u >> (8 * k));
    }
    return r;
}

}
