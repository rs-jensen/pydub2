#include <stdexcept>
#include <vector>

#include "third_party/ebur128.h"

#include "core.h"

namespace pydub2 {

double loudness(const Audio &a) {
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
