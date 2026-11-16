#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <string>
#include <stdexcept>
#include <vector>

#include <samplerate.h>

#include "core.h"

namespace pydub2 {

void resample_each(const Audio &a, int new_rate, const Cancel &cancel, const Piece &emit) {
    if (a.width < 1 || a.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    if (new_rate == a.rate) {
        emit(a.data);
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
    int err = 0;
    std::unique_ptr<SRC_STATE, decltype(&src_delete)> st(src_new(SRC_SINC_MEDIUM_QUALITY, ch, &err), src_delete);
    if (!st) {
        throw std::runtime_error(src_strerror(err));
    }
    SRC_DATA d = {};
    d.src_ratio = (double)new_rate / a.rate;
    // about a million frames in or out at a time
    long long step = d.src_ratio > 1 ? std::max<long long>(4096, (long long)((1 << 20) / d.src_ratio)) : 1 << 20;
    long long room = (long long)(step * d.src_ratio) + 4096, made = 0;
    std::vector<float> in((size_t)(step * ch)), out((size_t)(room * ch));
    std::string bytes;
    long long lo = -full, hi = full - 1;
    auto send = [&](long long frames) {
        bytes.resize((size_t)(frames * ch) * w);
        for (size_t i = 0; i < (size_t)(frames * ch); i++) {
            long long v = std::llround(out[i] * (float)full);
            v = std::min(hi, std::max(lo, v)); // clip, never wrap
            uint64_t u = (uint64_t)v;
            for (size_t k = 0; k < w; k++) {
                bytes[i * w + k] = (char)(u >> (8 * k));
            }
        }
        if (frames) {
            emit(bytes);
        }
    };
    for (long long used = 0; !err && made < out_n;) {
        long long k = std::min(step, n - used), at = 0;
        for (size_t i = 0; i < (size_t)(k * ch); i++) {
            long long v = 0;
            for (size_t j = w; j--;) {
                v = v * 256 + (unsigned char)a.data[((size_t)used * ch + i) * w + j];
            }
            if (v >= full) {
                v -= 2 * full;
            }
            in[i] = (float)v / (float)full;
        }
        bool last = used + k == n;
        for (;;) {
            cancel();
            d.data_in = in.data() + at * ch;
            d.input_frames = k - at;
            d.end_of_input = last;
            d.data_out = out.data();
            d.output_frames = std::min(room, out_n - made);
            err = src_process(st.get(), &d);
            at += d.input_frames_used;
            made += d.output_frames_gen;
            send(d.output_frames_gen);
            // out of room before out of input, or the end of the input flushing more than fits
            bool more = at < k || (last && d.output_frames_gen == d.output_frames);
            if (err || made >= out_n || !more || (!d.input_frames_used && !d.output_frames_gen)) {
                break;
            }
        }
        used += k;
        if (last) {
            break;
        }
    }
    if (err) {
        throw std::runtime_error(src_strerror(err));
    }
    // zeros stay in the result when libsamplerate makes fewer frames than out_n
    while (made < out_n) {
        long long k = std::min(room, out_n - made);
        bytes.assign((size_t)(k * ch) * w, 0);
        emit(bytes);
        made += k;
    }
}

void resample(const Audio &a, int new_rate, const Cancel &cancel, const Alloc &alloc) {
    char *res = NULL;
    size_t at = 0;
    resample_each(a, new_rate, cancel, [&](std::string_view piece) {
        if (!res) {
            size_t frames = (size_t)std::llrint((double)(a.data.size() / (a.width * a.channels)) * new_rate / a.rate);
            res = alloc(new_rate == a.rate ? a.data.size() : frames * a.channels * a.width);
        }
        memcpy(res + at, piece.data(), piece.size());
        at += piece.size();
    });
    if (!res) {
        alloc(0);
    }
}

}
