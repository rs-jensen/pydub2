#include <algorithm>
#include <cmath>
#include <cstdint>
#include <stdexcept>
#include <vector>

#include "core.h"

namespace pydub2 {

// sum of c*t over n samples and the energy of c, four sums so the loop runs in parallel
static float score(const float *c, const float *t, long long n) {
    float xy[4] = {}, e[4] = {};
    long long j = 0;
    for (; j + 4 <= n; j += 4) {
        for (int i = 0; i < 4; i++) {
            xy[i] += c[j + i] * t[j + i];
            e[i] += c[j + i] * c[j + i];
        }
    }
    for (; j < n; j++) {
        xy[0] += c[j] * t[j];
        e[0] += c[j] * c[j];
    }
    return (xy[0] + xy[1] + xy[2] + xy[3]) / std::sqrt(e[0] + e[1] + e[2] + e[3] + 1e-9f);
}

void stretch(const Audio &a, double rate, const Cancel &cancel, const Alloc &alloc) {
    if (!(rate > 0) || !std::isfinite(rate)) {
        throw std::invalid_argument("bad rate");
    }
    if (a.width < 1 || a.width > 4) {
        throw std::invalid_argument("bad sample width");
    }
    long long w = (long long)a.width, ch = (long long)a.channels;
    long long n = (long long)(a.data.size() / (a.width * a.channels));
    long long m = std::llround((double)n / rate);
    // 30 ms windows that overlap by half, smaller when the audio is only a few windows long
    long long hs = std::max<long long>(1, std::min<long long>(std::llround(a.rate * 0.015), n / 4));
    long long len = 2 * hs, d = hs / 2; // d is how far a window may move to line up, 7.5 ms
    long long step = std::max<long long>(1, a.rate / 12000); // the coarse search looks at every step-th sample
    long long last = m ? (m - 1) / hs : 0;
    // where window k starts in the input, its middle lines up with the middle of where it goes. the first starts at 0
    auto at = [&](long long k) { return k ? std::max(0LL, std::llround((double)((k + 1) * hs) * rate) - hs) : 0; };
    // zeros around the input, so no window or search reads outside it
    long long pad = d, total = pad + std::max(n, at(last) + d + len) + 1;

    double full = (double)(1LL << (8 * w - 1));
    std::vector<float> x((size_t)(total * ch)), mono((size_t)total);
    const unsigned char *s = (const unsigned char *)a.data.data();
    for (long long i = 0; i < n * ch; i++, s += w) {
        uint32_t u = 0;
        for (long long k = w; k--;) {
            u = u << 8 | s[k];
        }
        x[(size_t)(pad * ch + i)] = (float)((int32_t)(u << (32 - 8 * w)) >> (32 - 8 * w)) / (float)full;
    }
    // the search runs on one mix of the channels. a channel that is out of phase with the mix so far
    // is turned around first, or left and inverted right would cancel and leave nothing to line up
    for (long long c = 0; c < ch; c++) {
        double dot = 0;
        for (long long i = pad; i < n + pad; i++) {
            dot += (double)mono[(size_t)i] * x[(size_t)(i * ch + c)];
        }
        float sign = dot < 0 ? -1.f : 1.f;
        for (long long i = pad; i < n + pad; i++) {
            mono[(size_t)i] += sign * x[(size_t)(i * ch + c)];
        }
    }

    const double pi = 3.14159265358979323846;
    std::vector<float> win((size_t)len), y((size_t)(m * ch)), ws((size_t)m), nat((size_t)hs), cand;
    for (long long j = 0; j < len; j++) {
        win[(size_t)j] = (float)(0.5 - 0.5 * std::cos(2 * pi * (j + 0.5) / len)); // never 0, two halves add up to 1
    }
    long long prev = 0, q = 0;
    for (long long k = 0; k <= last && m; k++, prev = q) {
        cancel();
        long long p = at(k);
        long long o = k * hs, e = std::min(len, m - o);
        q = p;
        if (k) {
            // the window that lines up best with where the previous one would have gone on
            const float *t = &mono[(size_t)(prev + hs + pad)];
            long long lo = p - d, hi = p + d, nd = hs / step;
            // the last windows would take zeros from behind the input, so their range ends where the input does
            long long over = hi - std::max(0LL, n - e);
            if (over > 0) {
                hi -= over;
                lo = std::max(-d, lo - over);
            }
            for (long long j = 0; j < nd; j++) {
                nat[(size_t)j] = t[j * step];
            }
            cand.assign((size_t)((hi - lo + hs) / step + 1), 0.f);
            // every step-th sample of the range, so the coarse pass reads one row
            for (size_t j = 0; j < cand.size(); j++) {
                cand[j] = mono[(size_t)(lo + pad) + j * step];
            }
            float best = -INFINITY;
            for (long long o = 0; lo + o * step <= hi; o++) {
                float v = score(&cand[(size_t)o], nat.data(), nd);
                if (v > best) {
                    best = v;
                    q = lo + o * step;
                }
            }
            long long c = q;
            best = -INFINITY;
            for (long long r = std::max(lo, c - step + 1); r <= std::min(hi, c + step - 1); r++) {
                float v = score(&mono[(size_t)(r + pad)], t, hs);
                if (v > best) {
                    best = v;
                    q = r;
                }
            }
        }
        const float *src = &x[(size_t)((q + pad) * ch)];
        for (long long j = 0; j < e; j++) {
            ws[(size_t)(o + j)] += win[(size_t)j];
            for (long long c = 0; c < ch; c++) {
                y[(size_t)((o + j) * ch + c)] += win[(size_t)j] * src[j * ch + c];
            }
        }
    }

    char *out = alloc((size_t)(m * ch * w));
    long long lo = -(long long)full, hi = (long long)full - 1;
    for (long long i = 0; i < m * ch; i++) {
        long long v = std::llround((double)(y[(size_t)i] / ws[(size_t)(i / ch)]) * full);
        uint64_t u = (uint64_t)std::min(hi, std::max(lo, v));
        for (long long k = 0; k < w; k++) {
            *out++ = (char)(u >> (8 * k));
        }
    }
}

} // namespace pydub2
