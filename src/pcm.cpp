#include <cmath>
#include <cstring>
#include <algorithm>
#include <new>
#include <numeric>
#include <optional>
#include <stdexcept>
#include <type_traits>

#include "core.h"

namespace pydub2 {

static const int maxvals[] = {0, 0x7F, 0x7FFF, 0x7FFFFF, 0x7FFFFFFF};
static const int minvals[] = {0, -0x80, -0x8000, -0x800000, INT32_MIN};

static void check(std::string_view data, size_t width, size_t frame = 1) {
    if (width < 1 || width > 4) {
        throw std::invalid_argument("bad width");
    }
    if (frame < 1 || frame > 65535) {
        throw std::invalid_argument("too many channels");
    }
    if (data.size() % (width * frame)) {
        throw std::invalid_argument("not a whole number of samples");
    }
}

static int get(const char *p, size_t width) {
    if (width == 1) {
        return (int8_t)*p;
    }
    if (width == 2) {
        int16_t v;
        memcpy(&v, p, 2);
        return v;
    }
    if (width == 3) {
        return (uint8_t)p[0] | (uint8_t)p[1] << 8 | (int8_t)p[2] * 65536;
    }
    int32_t v;
    memcpy(&v, p, 4);
    return v;
}

static void put(char *p, size_t width, int v) {
    if (width == 1) {
        *p = (char)v;
    } else if (width == 2) {
        memcpy(p, &v, 2);
    } else if (width == 3) {
        memcpy(p, &v, 3);
    } else {
        memcpy(p, &v, 4);
    }
}

// samples as 32 bit, the way audioop does it
static int get32(const char *p, size_t width) {
    return (int)((uint32_t)get(p, width) << (32 - 8 * width));
}

static void put32(char *p, size_t width, int v) {
    put(p, width, v >> (32 - 8 * width));
}

static double fbound(double val, double minval, double maxval) {
    if (val > maxval) {
        return maxval;
    }
    if (val < minval + 1) {
        return minval;
    }
    return val;
}

static int scale(double val, size_t width) {
    if (std::isnan(val)) {
        throw std::invalid_argument("not a number");
    }
    return (int)floor(fbound(val, minvals[width], maxvals[width]));
}

template <class F>
static void by_width(size_t width, F f) {
    if (width == 1) {
        f(std::integral_constant<size_t, 1>());
    } else if (width == 2) {
        f(std::integral_constant<size_t, 2>());
    } else if (width == 3) {
        f(std::integral_constant<size_t, 3>());
    } else {
        f(std::integral_constant<size_t, 4>());
    }
}

double sumsq(std::string_view data, size_t width, bool fused) {
    check(data, width);
    size_t n = data.size() / width, i = 0;
    double sum = 0;
    // up to 2^53 a double adds the squares exactly, so an integer sum is the same and vectorises. Past it they go in order
    if (width < 4) {
        by_width(width, [&](auto w) {
            constexpr size_t W = decltype(w)::value;
            if constexpr (W < 4) {
                constexpr uint64_t top = (uint64_t)1 << 53;
                uint64_t exact = 0;
                while (i < n) {
                    size_t m = std::min<size_t>(n - i, 4096);
                    uint64_t block = 0;
                    for (size_t k = 0; k < m; k++) {
                        int64_t v = get(&data[(i + k) * W], W);
                        block += (uint64_t)(v * v);
                    }
                    if (block > top - exact) {
                        break;
                    }
                    exact += block;
                    i += m;
                }
                sum = (double)exact;
            }
        });
    }
    for (; i < n; i++) {
        double val = get(&data[i * width], width);
        sum = fused ? std::fma(val, val, sum) : sum + val * val;
    }
    return sum;
}

long long rms(std::string_view data, size_t width, bool fused) {
    double sum = sumsq(data, width, fused);
    if (data.empty()) {
        return 0;
    }
    return (unsigned int)sqrt(sum / (double)(data.size() / width));
}

long long max(std::string_view data, size_t width) {
    check(data, width);
    unsigned int m = 0;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0; i < data.size(); i += W) {
            int val = get(&data[i], W);
            unsigned int a = val < 0 ? 0u - (unsigned int)val : (unsigned int)val;
            m = a > m ? a : m;
        }
    });
    return m;
}

// the floor of a product held to the sample range, the way audioop's mul and tomono end. nan is set when it is not a number
template <size_t W>
static inline int floored(double v, bool &nan) {
    bool bad = v != v;
    nan |= bad;
    v = v > maxvals[W] ? (double)maxvals[W] : v;
    v = v < minvals[W] + 1.0 ? (double)minvals[W] : v;
    v = bad ? 0.0 : v;
    // v is within the range of an int, so the cast cannot overflow
    int i = (int)v;
    return i - (v < (double)i);
}

template <size_t W>
static inline int gain1(int x, double factor, bool &nan) {
    return floored<W>((double)x * factor, nan);
}

// count samples scaled from in to out, they may be the same memory. true if one of them came out not a number
template <size_t W>
static bool gain_run(const char *in, char *out, size_t count, double factor) {
    bool nan = false;
    if constexpr (W < 3) {
        // one and two byte samples take few values, so each is worked out once
        if (count >= (size_t)2 << (8 * W)) {
            using T = std::conditional_t<W == 1, int8_t, int16_t>;
            std::vector<T> table((size_t)1 << (8 * W));
            bool bad = false;
            for (size_t u = 0; u < table.size(); u++) {
                table[u] = (T)gain1<W>(W == 1 ? (int8_t)(uint8_t)u : (int16_t)(uint16_t)u, factor, bad);
            }
            if (!bad) {
                for (size_t k = 0; k < count; k++) {
                    std::conditional_t<W == 1, uint8_t, uint16_t> u;
                    memcpy(&u, in + k * W, W);
                    T v = table[u];
                    memcpy(out + k * W, &v, W);
                }
                return false;
            }
        }
    }
    for (size_t k = 0; k < count; k++) {
        put(out + k * W, W, gain1<W>(get(in + k * W, W), factor, nan));
    }
    return nan;
}

void mul(std::string_view data, size_t width, double factor, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    bool nan = false;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        nan = gain_run<W>(data.data(), out, data.size() / W, factor);
    });
    if (nan) {
        throw std::invalid_argument("not a number");
    }
}

void add(std::string_view a, std::string_view b, size_t width, const Alloc &alloc) {
    check(a, width);
    if (a.size() != b.size()) {
        throw std::invalid_argument("lengths should be the same");
    }
    char *out = alloc(a.size());
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0; i < a.size(); i += W) {
            long long sum = (long long)get(&a[i], W) + get(&b[i], W);
            put(out + i, W, (int)std::min<long long>(std::max<long long>(sum, minvals[W]), maxvals[W]));
        }
    });
}

void lin2lin(std::string_view data, size_t width, size_t new_width, const Alloc &alloc, bool nearest) {
    // nearest also takes the 64 bit samples of a double wav down to 24 bit or less
    bool wide = nearest && width == 8 && new_width < 4;
    if (!wide) {
        check(data, width);
    } else if (data.size() % 8) {
        throw std::invalid_argument("not a whole number of samples");
    }
    check("", new_width);
    char *out = alloc(data.size() / width * new_width);
    bool narrow = nearest && new_width < width;
    if (!narrow) {
        by_width(width, [&](auto w) {
            constexpr size_t W = decltype(w)::value;
            by_width(new_width, [&](auto nw) {
                constexpr size_t N = decltype(nw)::value;
                size_t count = data.size() / W;
                for (size_t k = 0; k < count; k++) {
                    put32(out + k * N, N, get32(&data[k * W], W));
                }
            });
        });
        return;
    }
    int64_t top = ((int64_t)1 << (8 * new_width - 1)) - 1;
    int shift = 32 - 8 * (int)new_width;
    for (size_t i = 0, j = 0; i < data.size(); i += width, j += new_width) {
        int64_t v;
        if (wide) {
            // the high half, with its lowest bit set when the low half is not 0, rounds like the whole sample
            memcpy(&v, &data[i], 8);
            v = (v >> 32) | ((v & 0xFFFFFFFF) != 0);
        } else {
            v = get32(&data[i], width);
        }
        // ties go to the even sample like lrintf, where ffmpeg rounds a float
        v = (v + ((int64_t)1 << (shift - 1)) - 1 + ((v >> shift) & 1)) >> shift;
        put(out + j, new_width, (int)std::min(top, std::max(-top - 1, v)));
    }
}

void mix(std::string_view head, std::string_view tail, std::string_view over, size_t width, size_t n, bool gain, double factor,
         const Alloc &alloc) {
    check(tail, width);
    check(over, width);
    if (n > tail.size() || n % width || (n && over.empty())) {
        throw std::invalid_argument("bad length to mix");
    }
    char *out = alloc(head.size() + tail.size());
    memcpy(out, head.data(), head.size());
    out += head.size();
    memcpy(out, tail.data(), tail.size());
    bool nan = false;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0, j = 0; i < n; i += W, j += W) {
            if (j == over.size()) {
                j = 0;
            }
            int x = get(&tail[i], W);
            if (gain) {
                x = gain1<W>(x, factor, nan);
            }
            long long sum = (long long)x + get(&over[j], W);
            put(out + i, W, (int)std::min<long long>(std::max<long long>(sum, minvals[W]), maxvals[W]));
        }
    });
    if (nan) {
        throw std::invalid_argument("not a number");
    }
}

bool fade(std::string_view before, std::optional<double> before_gain, std::string_view data, size_t width, size_t channels,
          double per_ms, long long start, long long count, double from_power, double step, std::string_view after,
          std::optional<double> after_gain, const Alloc &alloc) {
    check(before, width);
    check(after, width);
    check(data, width, channels);
    size_t fw = width * channels, frames = data.size() / fw;
    // positions are exact in a double and a sum of two cannot wrap
    constexpr long long exact = 1LL << 53;
    if (start < 0 || start > exact || count < 0 || count > exact || !(per_ms >= 0.5)) {
        return false;
    }
    // where the frames of the chunk for millisecond ms start, the way AudioSegment works them out
    auto at = [&](long long ms, long long &frame) {
        double v = (double)ms * per_ms;
        if (!(v < 4e18)) {
            return false;
        }
        frame = (long long)v;
        return true;
    };
    // a chunk short of frames at the end of the data is filled with silence, but not more than 2 ms of it
    struct Chunk {
        size_t from, have, fill;
    };
    auto chunk = [&](long long i, Chunk &c) {
        long long a, b;
        if (!at(start + i, a) || !at(start + i + 1, b) || b < a) {
            return false;
        }
        size_t first = std::min<unsigned long long>((unsigned long long)a, frames);
        size_t last = std::min<unsigned long long>((unsigned long long)b, frames);
        size_t missing = (size_t)(b - a) - (last - first);
        if ((double)missing > 2 * per_ms) {
            return false;
        }
        c.from = first;
        c.have = last - first;
        c.fill = c.have ? missing : 0;
        return true;
    };
    size_t total = before.size();
    if (after.size() > SIZE_MAX - total) {
        throw std::bad_alloc();
    }
    total += after.size();
    for (long long i = 0; i < count; i++) {
        Chunk c{};
        if (!chunk(i, c)) {
            return false;
        }
        // from here on the chunks are empty
        if (c.from == frames) {
            break;
        }
        size_t n = (c.have + c.fill) * fw;
        if (n > SIZE_MAX - total) {
            throw std::bad_alloc();
        }
        total += n;
    }
    char *out = alloc(total);
    bool nan = false;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        auto copy = [&](std::string_view src, std::optional<double> gain) {
            if (gain) {
                // msvc does not take W of the enclosing lambda as a constant here
                nan |= gain_run<decltype(w)::value>(src.data(), out, src.size() / decltype(w)::value, *gain);
            } else if (!src.empty()) {
                memcpy(out, src.data(), src.size());
            }
            out += src.size();
        };
        copy(before, before_gain);
        for (long long i = 0; i < count; i++) {
            Chunk c{};
            chunk(i, c);
            if (c.from == frames) {
                break;
            }
            double factor = from_power + step * (double)i;
            size_t bytes = c.have * fw, pad = c.fill * fw;
            nan |= gain_run<W>(data.data() + c.from * fw, out, bytes / W, factor);
            // silence times a factor that is not finite is not a number either
            nan |= pad && !std::isfinite(factor);
            memset(out + bytes, 0, pad);
            out += bytes + pad;
        }
        copy(after, after_gain);
    });
    if (nan) {
        throw std::invalid_argument("not a number");
    }
    return true;
}

void channel(std::string_view data, size_t width, size_t channels, size_t index, const Alloc &alloc) {
    check(data, width, 1);
    check({}, width, channels);
    // a partial last frame counts, the channels it reaches are one sample longer
    size_t samples = data.size() / width;
    size_t count = index < samples ? (samples - index + channels - 1) / channels : 0;
    char *out = alloc(count * width);
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t k = 0; k < count; k++) {
            memcpy(out + k * W, &data[(index + k * channels) * W], W);
        }
    });
}

void interleave(const std::vector<std::string_view> &parts, size_t width, size_t frames, const Alloc &alloc) {
    check({}, width, parts.size());
    for (auto &p : parts) {
        check(p, width);
        if (p.size() / width > frames) {
            throw std::invalid_argument("channel longer than the frames");
        }
    }
    size_t channels = parts.size();
    if (frames > SIZE_MAX / (width * channels)) {
        throw std::bad_alloc();
    }
    char *out = alloc(frames * channels * width);
    memset(out, 0, frames * channels * width);
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t c = 0; c < channels; c++) {
            for (size_t k = 0; k < parts[c].size() / W; k++) {
                memcpy(out + (k * channels + c) * W, &parts[c][k * W], W);
            }
        }
    });
}

void widen24(std::string_view data, size_t width, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size() / width * 4);
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0; i < data.size(); i += W, out += 4) {
            int v = get32(&data[i], W);
            // the low byte is padding, 0xff for negative samples
            out[0] = v < 0 ? (char)0xff : 0;
            out[1] = (char)(v >> 8);
            out[2] = (char)(v >> 16);
            out[3] = (char)(v >> 24);
        }
    });
}

void tomono(std::string_view data, size_t width, double lfac, double rfac, const Alloc &alloc) {
    check(data, width, 2);
    char *out = alloc(data.size() / 2);
    bool nan = false;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0; i < data.size(); i += W * 2) {
            double val = (double)get(&data[i], W) * lfac + (double)get(&data[i + W], W) * rfac;
            put(out + i / 2, W, floored<W>(val, nan));
        }
    });
    if (nan) {
        throw std::invalid_argument("not a number");
    }
}

void tostereo(std::string_view data, size_t width, double lfac, double rfac, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size() * 2);
    bool nan = false;
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        for (size_t i = 0; i < data.size(); i += W) {
            int val = get(&data[i], W);
            put(out + i * 2, W, gain1<W>(val, lfac, nan));
            put(out + i * 2 + W, W, gain1<W>(val, rfac, nan));
        }
    });
    if (nan) {
        throw std::invalid_argument("not a number");
    }
}

void downmix(std::string_view data, size_t width, size_t channels, const Alloc &alloc) {
    check({}, width, channels);
    check(data, width);
    long long n = (long long)channels;
    // a partial last frame is left out, like pydub does
    size_t frames = data.size() / (width * channels);
    char *out = alloc(frames * width);
    for (size_t i = 0, j = 0; j < frames * width; i += width * channels, j += width) {
        long long sum = 0;
        for (size_t c = 0; c < channels; c++) {
            long long v = get(&data[i + c * width], width);
            sum += v / n - (v % n < 0);
            if (sum < minvals[width] || sum > maxvals[width]) {
                throw std::overflow_error("sample out of range");
            }
        }
        memcpy(out + j, &sum, width);
    }
}

void reverse(std::string_view data, size_t width, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    by_width(width, [&](auto w) {
        constexpr size_t W = decltype(w)::value;
        size_t count = data.size() / W;
        for (size_t k = 0; k < count; k++) {
            memcpy(out + (count - 1 - k) * W, &data[k * W], W);
        }
    });
}

int avg(std::string_view data, size_t width, size_t step, size_t first) {
    check(data, width);
    if (step < 1) {
        throw std::invalid_argument("step should be >= 1");
    }
    size_t n = data.size() / width;
    if (first >= n) {
        return 0;
    }
    // counted so a step near the top of size_t cannot wrap the index
    size_t count = (n - first - 1) / step + 1;
    double sum = 0;
    for (size_t k = 0; k < count; k++) {
        sum += get(&data[(first + k * step) * width], width);
    }
    return (int)floor(sum / (double)count);
}

void bias(std::string_view data, size_t width, int bias, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    for (size_t i = 0; i < data.size(); i += width) {
        put(out + i, width, (int)((uint32_t)get(&data[i], width) + (uint32_t)bias));
    }
}

void bias_pairs(std::string_view data, size_t width, int even, int odd, const Alloc &alloc) {
    check(data, width, 2);
    char *out = alloc(data.size());
    for (size_t i = 0; i < data.size(); i += width) {
        put(out + i, width, (int)((uint32_t)get(&data[i], width) + (uint32_t)(i / width % 2 ? odd : even)));
    }
}

void ratecv(std::string_view data, size_t width, size_t channels, int inrate, int outrate, const Alloc &alloc) {
    if (channels < 1) {
        throw std::invalid_argument("channels should be >= 1");
    }
    check(data, width, channels);
    if (inrate <= 0 || outrate <= 0) {
        throw std::invalid_argument("sampling rate not > 0");
    }
    int g = std::gcd(inrate, outrate);
    inrate /= g;
    outrate /= g;
    std::vector<int> prev(channels, 0), cur(channels, 0);
    const char *p = data.data();
    size_t len = data.size() / (width * channels);
    // one frame for the first input and one more each time the rate steps past an input, so the output is sized once
    uint64_t frames = 0;
    if (len) {
        uint64_t q = (len - 1) / inrate;
        if (q && (uint64_t)outrate > UINT64_MAX / q) {
            throw std::bad_alloc();
        }
        frames = q * outrate + (len - 1) % inrate * (uint64_t)outrate / inrate + 1;
    }
    if (frames > SIZE_MAX / (width * channels)) {
        throw std::bad_alloc();
    }
    size_t bytes = (size_t)frames * width * channels;
    char *out = alloc(bytes), *at = out, *end = out + bytes;
    int d = -outrate;
    for (;;) {
        while (d < 0) {
            if (len == 0) {
                if (at != end) {
                    throw std::logic_error("resampled length");
                }
                return;
            }
            for (size_t c = 0; c < channels; c++) {
                prev[c] = cur[c];
                cur[c] = get32(p, width);
                p += width;
            }
            len--;
            d += outrate;
        }
        while (d >= 0) {
            for (size_t c = 0; c < channels; c++) {
                int v = (int)(((double)prev[c] * (double)d + (double)cur[c] * (double)(outrate - d)) / (double)outrate);
                if (at < end) {
                    put32(at, width, v);
                }
                at += width;
            }
            d -= inrate;
        }
    }
}

// the same walk as the python loops in pydub, one float operation after another
static int toint(double v, size_t width) {
    if (std::isnan(v)) {
        throw std::invalid_argument("not a number");
    }
    if (!(v > -4e18 && v < 4e18) || (long long)v < minvals[width] || (long long)v > maxvals[width]) {
        throw std::overflow_error("sample out of range");
    }
    return (int)(long long)v;
}

void lowpass(std::string_view data, size_t width, size_t channels, double alpha, const Alloc &alloc) {
    check({}, width, channels);
    check(data, width);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels;
    std::vector<double> last(channels);
    for (size_t c = 0; c < channels && n; c++) {
        last[c] = get(&data[c * width], width);
    }
    for (size_t i = 1; i < n; i++) {
        for (size_t c = 0; c < channels; c++) {
            size_t at = (i * channels + c) * width;
            last[c] = last[c] + alpha * ((double)get(&data[at], width) - last[c]);
            put(out + at, width, toint(last[c], width));
        }
    }
}

void highpass(std::string_view data, size_t width, size_t channels, double alpha, const Alloc &alloc) {
    check({}, width, channels);
    check(data, width);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels;
    std::vector<double> last(channels);
    for (size_t c = 0; c < channels && n; c++) {
        last[c] = get(&data[c * width], width);
    }
    for (size_t i = 1; i < n; i++) {
        for (size_t c = 0; c < channels; c++) {
            size_t at = (i * channels + c) * width;
            last[c] = alpha * ((last[c] + (double)get(&data[at], width)) - (double)get(&data[at - channels * width], width));
            double v = last[c];
            if ((double)minvals[width] > v) {
                v = minvals[width];
            }
            if ((double)maxvals[width] < v) {
                v = maxvals[width];
            }
            put(out + at, width, toint(v, width));
        }
    }
}

void compress(std::string_view data, size_t width, size_t channels, double thresh, double ratio, double attack_frames,
              double release_frames, long long look, bool fused, const Cancel &cancel, const Alloc &alloc) {
    check(data, width, channels);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels, fw = width * channels;
    size_t lk = look > 0 ? (size_t)look : 0;
    // below 2^53 the sum is exact and can slide, above it rms rounds as it adds so it is asked each time
    bool exact = (double)std::min(lk, n) * (double)channels * (double)(1ULL << (16 * width - 2)) < 9007199254740992.0;
    auto frame = [&](size_t f) {
        long long sum = 0;
        for (size_t c = 0; c < channels; c++) {
            long long v = get(&data[f * fw + c * width], width);
            sum += v * v;
        }
        return sum;
    };
    long long sum = 0;
    size_t hi = 0, from = 0;
    double atten = 0.0;
    for (size_t i = 0; i < n; i++) {
        if (i % 4096 == 0) {
            cancel();
        }
        size_t lo = lk == 0 ? i : i > lk ? i - lk : 0;
        unsigned int now = 0;
        if (i > lo && exact) {
            while (hi < i) {
                sum += frame(hi++);
            }
            while (from < lo) {
                sum -= frame(from++);
            }
            now = (unsigned int)sqrt((double)sum / (double)((i - lo) * channels));
        } else if (i > lo) {
            now = (unsigned int)rms(data.substr(lo * fw, (i - lo) * fw), width, fused);
        }
        if (atten == 0.0 && now <= thresh) {
            continue;
        }
        double db = 0.0;
        if (now != 0) {
            if (thresh == 0) {
                throw std::domain_error("float division by zero");
            }
            db = 20 * (std::log(now / thresh) / std::log(10.0));
            if (0 > db) {
                db = 0;
            }
        }
        double most = (1 - (1.0 / ratio)) * db;
        if (now > thresh && atten <= most) {
            atten += most / attack_frames;
            if (most < atten) {
                atten = most;
            }
        } else {
            atten -= most / release_frames;
            if (0 > atten) {
                atten = 0;
            }
        }
        if (atten != 0.0) {
            double factor = std::pow(10.0, -atten / 20);
            for (size_t c = 0; c < channels; c++) {
                size_t at = i * fw + c * width;
                put(out + at, width, scale(get(&data[at], width) * factor, width));
            }
        }
    }
}

}
