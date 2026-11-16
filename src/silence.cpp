#include <algorithm>
#include <climits>
#include <cmath>
#include <cstring>
#include <stdexcept>

#include "core.h"

namespace pydub2 {

static int64_t sample(const char *p, int width) {
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

// is the 128 bit number hi:lo below a * b. no __int128 on msvc
static bool mul_less(uint64_t hi, uint64_t lo, uint64_t a, uint64_t b) {
    uint64_t a0 = (uint32_t)a, a1 = a >> 32, b0 = (uint32_t)b, b1 = b >> 32;
    uint64_t p00 = a0 * b0, p01 = a0 * b1, p10 = a1 * b0, p11 = a1 * b1;
    uint64_t mid = (p00 >> 32) + (uint32_t)p01 + (uint32_t)p10;
    uint64_t plo = (uint32_t)p00 | mid << 32;
    uint64_t phi = p11 + (p01 >> 32) + (p10 >> 32) + (mid >> 32);
    return hi < phi || (hi == phi && lo < plo);
}

Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step, const Cancel &cancel) {
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
    size_t fw = (size_t)a.channels * a.width;
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

    // pydub compares the truncated rms, so rms <= thresh is sum < (floor(thresh) + 1)^2 * n, all in integers
    // rms is never above 2^31 so a bigger thresh means everything is silent
    bool all = thresh >= 2147483648.0;
    uint64_t lim = thresh >= 0 && !all ? (uint64_t)thresh + 1 : 0;
    lim *= lim;

    // slice bounds truncate like _parse_position, and short slices count zeros at the end
    // the slices only move forward, so the 128 bit sum of squares is kept for the window and not for every ms
    double per_ms = a.rate / 1000.0;
    size_t total = a.data.size() / a.width;
    const char *p = a.data.data();
    int w = a.width;
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
        if (!(n == 0 ? thresh >= 0 : all || mul_less(h, l, lim, n))) {
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
