#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>
#include <stdexcept>

#include "core.h"

namespace pydub2 {

static int64_t sample(const char *p, size_t width) {
    if (width == 1) {
        return (int8_t)p[0];
    }
    if (width == 2) {
        return (int16_t)((uint8_t)p[0] | (uint8_t)p[1] << 8);
    }
    if (width == 3) {
        return (uint8_t)p[0] | (uint8_t)p[1] << 8 | (int8_t)p[2] * 65536;
    }
    int32_t v;
    memcpy(&v, p, 4);
    return v;
}

Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step, bool fused, const Cancel &cancel) {
    if (a.width < 1 || a.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    if (seek_step <= 0) {
        throw std::invalid_argument("seek_step must be positive");
    }
    if (min_silence_len < 0) {
        throw std::invalid_argument("min_silence_len must not be negative");
    }

    // python round, same as nearbyint
    size_t fw = a.channels * a.width;
    double frames = (double)(a.data.size() / fw);
    double ms = std::nearbyint(1000.0 * (frames / a.rate));
    if (ms > INT_MAX) {
        throw std::invalid_argument("too long for detect_silence");
    }
    int seg_len = (int)ms;
    if (seg_len < min_silence_len) {
        return {};
    }

    double thresh = std::pow(10.0, silence_thresh / 20) * (double)(1LL << (a.width * 8 - 1));

    // pydub compares the truncated rms, so rms <= thresh is about sum < (floor(thresh) + 1)^2 * n
    // audioop sums in a double that rounds, so close calls are decided by summing the slice like audioop
    // below 2^50 that sum is exact and one step is bigger than the sqrt and division can round, so no close calls
    // rms is never above 2^31 so a bigger thresh means everything is silent
    bool all = thresh >= 2147483648.0;
    uint64_t lim = thresh >= 0 && !all ? (uint64_t)thresh + 1 : 0;
    lim *= lim;

    // slice bounds truncate like _parse_position, and short slices count zeros at the end
    // the slices only move forward, so the 128 bit sum of squares is kept for the window and not for every ms
    double per_ms = a.rate / 1000.0;
    size_t total = a.data.size() / a.width;
    const char *p = a.data.data();
    size_t w = a.width;
    uint64_t h = 0, l = 0;
    size_t front = 0, back = 0;

    Ranges out;
    int cur = 0, prev = 0;
    bool any = false;
    auto check = [&](int i) {
        int j = i + min_silence_len;
        size_t s = (size_t)((double)i * per_ms), e = (size_t)((double)j * per_ms);
        size_t n = (e - s) * a.channels;
        for (size_t end = std::min(e * a.channels, total); front < end; front++) {
            int64_t v = sample(p + front * w, w);
            uint64_t q = (uint64_t)(v * v);
            l += q;
            h += l < q;
        }
        for (size_t start = std::min(s * a.channels, total); back < start; back++) {
            int64_t v = sample(p + back * w, w);
            uint64_t q = (uint64_t)(v * v);
            h -= l < q;
            l -= q;
        }
        bool quiet = n == 0 ? thresh >= 0 : all;
        if (n > 0 && !all) {
            double sum = (double)h * 18446744073709551616.0 + (double)l;
            double top = (double)lim * (double)n;
            double slack = top * ((double)n + 16) * 0x1p-51;
            if (top < 0x1p50 || sum < top - slack || sum > top + slack) {
                quiet = sum < top;
            } else {
                size_t from = std::min(s * a.channels, total), to = std::min(e * a.channels, total);
                quiet = (unsigned int)sqrt(sumsq(std::string_view(p + from * w, (to - from) * w), w, fused) / (double)n) <= thresh;
            }
        }
        if (!quiet) {
            return;
        }
        if (any) {
            // two blips can make one slice loud while the silence runs on
            bool continuous = i == (long long)prev + seek_step;
            bool gap = i > prev + min_silence_len;
            if (!continuous && gap) {
                out.push_back({cur, prev + min_silence_len});
                cur = i;
            }
        } else {
            cur = i;
            any = true;
        }
        prev = i;
    };

    int last = seg_len - min_silence_len;
    for (long long i = 0; i <= last; i += seek_step) {
        if ((i / seek_step & 4095) == 0) {
            cancel();
        }
        check((int)i);
    }
    // last slice is always checked, like pydub
    if (last % seek_step) {
        check(last);
    }

    if (any) {
        out.push_back({cur, prev + min_silence_len});
    }
    return out;
}

}
