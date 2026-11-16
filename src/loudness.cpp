#include <algorithm>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <vector>

#include "third_party/ebur128.h"

#include "core.h"

namespace pydub2 {

double loudness(const Audio &a, const Cancel &cancel) {
    if (a.width < 1 || a.width > 4)
        throw std::invalid_argument("bad sample width");
    // the library writes its constants at every init, so the first one runs alone
    static std::once_flag once;
    std::call_once(once, [] {
        ebur128_state *s = ebur128_init(1, 48000, EBUR128_MODE_I | EBUR128_MODE_HISTOGRAM);
        ebur128_destroy(&s);
    });
    ebur128_state *st = ebur128_init(a.channels, a.rate, EBUR128_MODE_I);
    if (!st) throw std::runtime_error("cant measure loudness");
    size_t frames = a.data.size() / (a.width * a.channels);
    int err = 0;
    try {
        size_t step = 1 << 15;
        for (size_t f = 0; !err && f < frames; f += step) {
            cancel();
            size_t n = std::min(step, frames - f), k = n * a.channels;
            const char *p = a.data.data() + f * a.channels * a.width;
            if (a.width == 1) {
                std::vector<short> buf(k);
                for (size_t i = 0; i < k; i++) buf[i] = ((int8_t)p[i]) * 256;
                err = ebur128_add_frames_short(st, buf.data(), n);
            } else if (a.width == 2) {
                err = ebur128_add_frames_short(st, (const short *)p, n);
            } else if (a.width == 3) {
                std::vector<int> buf(k);
                for (size_t i = 0; i < k; i++)
                    buf[i] = (int)((uint32_t)(uint8_t)p[i * 3] << 8 | (uint32_t)(uint8_t)p[i * 3 + 1] << 16 | (uint32_t)(uint8_t)p[i * 3 + 2] << 24);
                err = ebur128_add_frames_int(st, buf.data(), n);
            } else {
                err = ebur128_add_frames_int(st, (const int *)p, n);
            }
        }
    } catch (...) {
        ebur128_destroy(&st);
        throw;
    }
    double out = 0;
    if (!err) err = ebur128_loudness_global(st, &out);
    ebur128_destroy(&st);
    if (err) throw std::runtime_error("cant measure loudness");
    return out;
}

}
