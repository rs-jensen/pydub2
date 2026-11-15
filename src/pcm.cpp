#include <cmath>
#include <cstring>
#include <numeric>
#include <stdexcept>

#include "core.h"

namespace pydub2 {

static const int maxvals[] = {0, 0x7F, 0x7FFF, 0, 0x7FFFFFFF};
static const int minvals[] = {0, -0x80, -0x8000, 0, INT32_MIN};

static void check(const std::string &data, int width, int frame = 1) {
    if (width != 1 && width != 2 && width != 4) throw std::invalid_argument("bad width");
    if (data.size() % (width * frame)) throw std::invalid_argument("not a whole number of samples");
}

static int get(const char *p, int width) {
    if (width == 1) return (int8_t)*p;
    if (width == 2) {
        int16_t v;
        memcpy(&v, p, 2);
        return v;
    }
    int32_t v;
    memcpy(&v, p, 4);
    return v;
}

static void put(char *p, int width, int v) {
    if (width == 1) *p = (char)v;
    else if (width == 2) memcpy(p, &v, 2);
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
    return (int)floor(fbound(val, minvals[width], maxvals[width]));
}

long long rms(const std::string &data, int width) {
    check(data, width);
    double sum = 0;
    for (size_t i = 0; i < data.size(); i += width) {
        double val = get(&data[i], width);
        sum += val * val;
    }
    if (data.empty()) return 0;
    return (unsigned int)sqrt(sum / (double)(data.size() / width));
}

long long max(const std::string &data, int width) {
    check(data, width);
    unsigned int m = 0;
    for (size_t i = 0; i < data.size(); i += width) {
        int val = get(&data[i], width);
        unsigned int a = val < 0 ? 0u - (unsigned int)val : (unsigned int)val;
        if (a > m) m = a;
    }
    return m;
}

std::string mul(const std::string &data, int width, double factor) {
    check(data, width);
    std::string out(data.size(), 0);
    for (size_t i = 0; i < data.size(); i += width) {
        put(&out[i], width, scale(get(&data[i], width) * factor, width));
    }
    return out;
}

std::string add(const std::string &a, const std::string &b, int width) {
    check(a, width);
    if (a.size() != b.size()) throw std::invalid_argument("lengths should be the same");
    std::string out(a.size(), 0);
    for (size_t i = 0; i < a.size(); i += width) {
        put(&out[i], width, scale((double)get(&a[i], width) + (double)get(&b[i], width), width));
    }
    return out;
}

std::string lin2lin(const std::string &data, int width, int new_width) {
    check(data, width);
    check("", new_width);
    std::string out(data.size() / width * new_width, 0);
    for (size_t i = 0, j = 0; i < data.size(); i += width, j += new_width) {
        put32(&out[j], new_width, get32(&data[i], width));
    }
    return out;
}

std::string tomono(const std::string &data, int width, double lfac, double rfac) {
    check(data, width, 2);
    std::string out(data.size() / 2, 0);
    for (size_t i = 0; i < data.size(); i += width * 2) {
        double val = (double)get(&data[i], width) * lfac + (double)get(&data[i + width], width) * rfac;
        put(&out[i / 2], width, scale(val, width));
    }
    return out;
}

std::string tostereo(const std::string &data, int width, double lfac, double rfac) {
    check(data, width);
    std::string out(data.size() * 2, 0);
    for (size_t i = 0; i < data.size(); i += width) {
        double val = get(&data[i], width);
        put(&out[i * 2], width, scale(val * lfac, width));
        put(&out[i * 2 + width], width, scale(val * rfac, width));
    }
    return out;
}

std::string reverse(const std::string &data, int width) {
    check(data, width);
    std::string out(data.size(), 0);
    for (size_t i = 0; i < data.size(); i += width) {
        memcpy(&out[data.size() - width - i], &data[i], width);
    }
    return out;
}

int avg(const std::string &data, int width) {
    check(data, width);
    if (data.empty()) return 0;
    double sum = 0;
    for (size_t i = 0; i < data.size(); i += width) sum += get(&data[i], width);
    return (int)floor(sum / (double)(data.size() / width));
}

std::string bias(const std::string &data, int width, int bias) {
    check(data, width);
    std::string out(data.size(), 0);
    for (size_t i = 0; i < data.size(); i += width) {
        put(&out[i], width, (int)((uint32_t)get(&data[i], width) + (uint32_t)bias));
    }
    return out;
}

std::string ratecv(const std::string &data, int width, int channels, int inrate, int outrate) {
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
            if (len == 0) return out;
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

}
