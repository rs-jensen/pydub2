#include <cstdint>
#include <stdexcept>
#include <vector>

#include "third_party/ebur128.h"

#include "core.h"

namespace pydub2 {

double loudness(const Audio &a) {
    if (a.width < 1 || a.width > 4)
        throw std::invalid_argument("bad sample width");
    ebur128_state *st = ebur128_init(a.channels, a.rate, EBUR128_MODE_I);
    if (!st) throw std::runtime_error("cant measure loudness");
    size_t frames =a.data.size() / (a.width * a.channels);
    const char *p = a.data.data();
    int err;
    if (a.width == 1) {
        std::vector<short> buf(a.data.size());
        for (size_t i = 0; i < buf.size(); i++) buf[i] = ((int8_t)p[i]) * 256;
        err = ebur128_add_frames_short(st, buf.data(), frames);
    } else if (a.width == 2) {
        err = ebur128_add_frames_short(st, (const short *)p, frames);
    } else if (a.width == 3) {
        std::vector<int> buf(a.data.size() / 3);
        for (size_t i = 0; i < buf.size(); i++)
            buf[i] = (int)((uint32_t)(uint8_t)p[i * 3] << 8 | (uint32_t)(uint8_t)p[i * 3 + 1] << 16 | (uint32_t)(uint8_t)p[i * 3 + 2] << 24);
        err = ebur128_add_frames_int(st, buf.data(), frames);
    } else {
        err = ebur128_add_frames_int(st, (const int *)p, frames);
    }
    double out = 0;
    if (!err) err = ebur128_loudness_global(st, &out);
    ebur128_destroy(&st);
    if (err) throw std::runtime_error("cant measure loudness");
    return out;
}

}
