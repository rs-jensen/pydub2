#include <cmath>
#include <cstring>
#include <algorithm>
#include <numeric>
#include <stdexcept>

#include "core.h"

namespace pydub2 {

static const int maxvals[] = {0, 0x7F, 0x7FFF, 0x7FFFFF, 0x7FFFFFFF};
static const int minvals[] = {0, -0x80, -0x8000, -0x800000, INT32_MIN};

static void check(std::string_view data, int width, int frame = 1) {
    if (width < 1 || width > 4) throw std::invalid_argument("bad width");
    if (frame > 65535) throw std::invalid_argument("too many channels");
    if (data.size() % ((size_t)width * frame)) throw std::invalid_argument("not a whole number of samples");
}

static int get(const char *p, int width) {
    if (width == 1) return (int8_t)*p;
    if (width == 2) {
        int16_t v;
        memcpy(&v, p, 2);
        return v;
    }
    if (width == 3) return (uint8_t)p[0] | (uint8_t)p[1] << 8 | (int8_t)p[2] * 65536;
    int32_t v;
    memcpy(&v, p, 4);
    return v;
}

static void put(char *p, int width, int v) {
    if (width == 1) *p = (char)v;
    else if (width == 2) memcpy(p, &v, 2);
    else if (width == 3) memcpy(p, &v, 3);
    else memcpy(p, &v, 4);
}

// samples as 32 bit, the way audioop does it
static int get32(const char *p, int width) {
    return (int)((uint32_t)get(p, width) << (32 - 8 * width));
}

static void put32(char *p, int width, int v) {
    put(p, width, v >> (32 - 8 * width));
}

static double fbound(double val, double minval, double maxval) {
    if (val > maxval) return maxval;
    if (val < minval + 1) return minval;
    return val;
}

static int scale(double val, int width) {
    if (std::isnan(val)) throw std::invalid_argument("not a number");
    return (int)floor(fbound(val, minvals[width], maxvals[width]));
}

long long rms(std::string_view data, int width) {
    check(data, width);
    double sum = 0;
    for (size_t i = 0; i < data.size(); i += width) {
        double val = get(&data[i], width);
        sum += val * val;
    }
    if (data.empty()) return 0;
    return (unsigned int)sqrt(sum / (double)(data.size() / width));
}

long long max(std::string_view data, int width) {
    check(data, width);
    unsigned int m = 0;
    for (size_t i = 0; i < data.size(); i += width) {
        int val = get(&data[i], width);
        unsigned int a = val < 0 ? 0u - (unsigned int)val : (unsigned int)val;
        if (a > m) m = a;
    }
    return m;
}

void mul(std::string_view data, int width, double factor, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    for (size_t i = 0; i < data.size(); i += width) {
        put(out + i, width, scale(get(&data[i], width) * factor, width));
    }
}

void add(std::string_view a, std::string_view b, int width, const Alloc &alloc) {
    check(a, width);
    if (a.size() != b.size()) throw std::invalid_argument("lengths should be the same");
    char *out = alloc(a.size());
    for (size_t i = 0; i < a.size(); i += width) {
        put(out + i, width, scale((double)get(&a[i], width) + (double)get(&b[i], width), width));
    }
}

void lin2lin(std::string_view data, int width, int new_width, const Alloc &alloc) {
    check(data, width);
    check("", new_width);
    char *out = alloc(data.size() / width * new_width);
    for (size_t i = 0, j = 0; i < data.size(); i += width, j += new_width) {
        put32(out + j, new_width, get32(&data[i], width));
    }
}

void tomono(std::string_view data, int width, double lfac, double rfac, const Alloc &alloc) {
    check(data, width, 2);
    char *out = alloc(data.size() / 2);
    for (size_t i = 0; i < data.size(); i += width * 2) {
        double val = (double)get(&data[i], width) * lfac + (double)get(&data[i + width], width) * rfac;
        put(out + i / 2, width, scale(val, width));
    }
}

void tostereo(std::string_view data, int width, double lfac, double rfac, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size() * 2);
    for (size_t i = 0; i < data.size(); i += width) {
        double val = get(&data[i], width);
        put(out + i * 2, width, scale(val * lfac, width));
        put(out + i * 2 + width, width, scale(val * rfac, width));
    }
}

void reverse(std::string_view data, int width, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    for (size_t i = 0; i < data.size(); i += width) {
        memcpy(out + data.size() - width - i, &data[i], width);
    }
}

int avg(std::string_view data, int width) {
    check(data, width);
    if (data.empty()) return 0;
    double sum = 0;
    for (size_t i = 0; i < data.size(); i += width) sum += get(&data[i], width);
    return (int)floor(sum / (double)(data.size() / width));
}

void bias(std::string_view data, int width, int bias, const Alloc &alloc) {
    check(data, width);
    char *out = alloc(data.size());
    for (size_t i = 0; i < data.size(); i += width) {
        put(out + i, width, (int)((uint32_t)get(&data[i], width) + (uint32_t)bias));
    }
}

void ratecv(std::string_view data, int width, int channels, int inrate, int outrate, const Alloc &alloc) {
    if (channels < 1) throw std::invalid_argument("channels should be >= 1");
    check(data, width, channels);
    if (inrate <= 0 || outrate <= 0) throw std::invalid_argument("sampling rate not > 0");
    int g = std::gcd(inrate, outrate);
    inrate /= g;
    outrate /= g;
    std::vector<int> prev(channels, 0), cur(channels, 0);
    std::string out;
    const char *p = data.data();
    size_t len = data.size() / (width * channels);
    int d = -outrate;
    for (;;) {
        while (d < 0) {
            if (len == 0) {
                memcpy(alloc(out.size()), out.data(), out.size());
                return;
            }
            for (int c = 0; c < channels; c++) {
                prev[c] = cur[c];
                cur[c] = get32(p, width);
                p += width;
            }
            len--;
            d += outrate;
        }
        while (d >= 0) {
            for (int c = 0; c < channels; c++) {
                int v = (int)(((double)prev[c] * (double)d + (double)cur[c] * (double)(outrate - d)) / (double)outrate);
                out.resize(out.size() + width);
                put32(&out[out.size() - width], width, v);
            }
            d -= inrate;
        }
    }
}

// the same walk as the python loops in pydub, one float operation after another
static int toint(double v, int width) {
    if (std::isnan(v)) throw std::invalid_argument("not a number");
    if (!(v > -4e18 && v < 4e18) || (long long)v < minvals[width] || (long long)v > maxvals[width])
        throw std::overflow_error("sample out of range");
    return (int)(long long)v;
}

void lowpass(std::string_view data, int width, int channels, double alpha, const Alloc &alloc) {
    check(data, width, channels);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels;
    std::vector<double> last(channels);
    for (int c = 0; c < channels && n; c++) last[c] = get(&data[c * width], width);
    for (size_t i = 1; i < n; i++) {
        for (int c = 0; c < channels; c++) {
            size_t at = (i * channels + c) * width;
            last[c] = last[c] + alpha * ((double)get(&data[at], width) - last[c]);
            put(out + at, width, toint(last[c], width));
        }
    }
}

void highpass(std::string_view data, int width, int channels, double alpha, const Alloc &alloc) {
    check(data, width, channels);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels;
    std::vector<double> last(channels);
    for (int c = 0; c < channels && n; c++) last[c] = get(&data[c * width], width);
    for (size_t i = 1; i < n; i++) {
        for (int c = 0; c < channels; c++) {
            size_t at = (i * channels + c) * width;
            last[c] = alpha * ((last[c] + (double)get(&data[at], width)) - (double)get(&data[at - channels * width], width));
            double v = last[c];
            if ((double)minvals[width] > v) v = minvals[width];
            if ((double)maxvals[width] < v) v = maxvals[width];
            put(out + at, width, toint(v, width));
        }
    }
}

void compress(std::string_view data, int width, int channels, double thresh, double ratio, double attack_frames,
              double release_frames, long long look, const Alloc &alloc) {
    check(data, width, channels);
    char *out = alloc(data.size());
    memcpy(out, data.data(), data.size());
    size_t n = data.size() / width / channels, fw = (size_t)width * channels;
    // a sum of squares below 2^53 is exact, so it can slide along. above that rms rounds as it adds and has to be asked each time
    bool exact = (double)std::min<long long>(look, n) * channels * (double)(1ULL << (16 * width - 2)) < 9007199254740992.0;
    auto frame = [&](size_t f) {
        long long sum = 0;
        for (int c = 0; c < channels; c++) {
            long long v = get(&data[f * fw + c * width], width);
            sum += v * v;
        }
        return sum;
    };
    long long sum = 0;
    size_t hi = 0, from = 0;
    double atten = 0.0;
    for (size_t i = 0; i < n; i++) {
        size_t lo = look <= 0 ? i : i > (size_t)look ? i - look : 0;
        unsigned int now = 0;
        if (i > lo && exact) {
            while (hi < i) sum += frame(hi++);
            while (from < lo) sum -= frame(from++);
            now = (unsigned int)sqrt((double)sum / (double)((i - lo) * channels));
        } else if (i > lo) {
            now = (unsigned int)rms(data.substr(lo * fw, (i - lo) * fw), width);
        }
        if (atten == 0.0 && now <= thresh) continue;
        double db = 0.0;
        if (now != 0) {
            db = 20 * (std::log(now / thresh) / std::log(10.0));
            if (0 > db) db = 0;
        }
        double most = (1 - (1.0 / ratio)) * db;
        if (now > thresh && atten <= most) {
            atten += most / attack_frames;
            if (most < atten) atten = most;
        } else {
            atten -= most / release_frames;
            if (0 > atten) atten = 0;
        }
        if (atten != 0.0) {
            double factor = std::pow(10.0, -atten / 20);
            for (int c = 0; c < channels; c++) {
                size_t at = i * fw + c * width;
                put(out + at, width, scale(get(&data[at], width) * factor, width));
            }
        }
    }
}

}
