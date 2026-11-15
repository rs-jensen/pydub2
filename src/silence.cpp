#include <algorithm>
#include <cmath>
#include <stdexcept>

#include "core.h"

namespace pydub2 {

Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step) {
    if (seek_step <= 0) throw std::invalid_argument("seek_step must be positive");

    // python round, same as nearbyint
    int fw = a.channels * a.width;
    double frames = (double)(a.data.size() / fw);
    int seg_len = (int)std::nearbyint(1000.0 * (frames / a.rate));
    if (seg_len < min_silence_len) return {};

    double thresh = std::pow(10.0, silence_thresh / 20) * (double)(1LL << (a.width * 8 - 1));

    // slice bounds truncate like _parse_position, and short slices get zeros at the end
    double per_ms = a.rate / 1000.0;
    size_t n = a.data.size();
    std::vector<int> silent;
    auto check = [&](int i) {
        size_t b = (size_t)((double)i * per_ms) * fw;
        size_t e = (size_t)((double)(i + min_silence_len) * per_ms) * fw;
        std::string s = a.data.substr(std::min(b, n), std::min(e, n) - std::min(b, n));
        s.resize(e - b, '\0');
        if (rms(s, a.width) <= thresh) silent.push_back(i);
    };

    int last = seg_len - min_silence_len;
    for (int i = 0; i <= last; i += seek_step) check(i);
    // last slice is always checked, like pydub
    if (last % seek_step) check(last);

    if (silent.empty()) return {};

    Ranges out;
    int prev = silent[0];
    int cur = prev;
    for (size_t k = 1; k < silent.size(); k++) {
        int s = silent[k];
        bool continuous = s == prev + seek_step;
        // two blips can make one slice loud while the silence runs on
        bool gap = s > prev + min_silence_len;
        if (!continuous && gap) {
            out.push_back({cur, prev + min_silence_len});
            cur = s;
        }
        prev = s;
    }
    out.push_back({cur, prev + min_silence_len});
    return out;
}

}
