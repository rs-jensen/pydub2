#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include "core.h"

namespace nb = nanobind;
using namespace pydub2;

static std::string str(nb::bytes b) {
    return std::string(b.c_str(), b.size());
}

static nb::bytes bytes(const std::string &s) {
    return nb::bytes(s.data(), s.size());
}

static Audio audio(nb::bytes data, int rate, int channels, int width) {
    if (rate < 1 || channels < 1 || (width != 1 && width != 2 && width != 4))
        throw std::invalid_argument("bad rate, channels or sample width");
    Audio a;
    a.data = str(data);
    if (a.data.size() % ((size_t)channels * width))
        throw std::invalid_argument("data is not a whole number of frames");
    a.rate = rate;
    a.channels = channels;
    a.width = width;
    return a;
}

static nb::list ranges(const Ranges &r) {
    nb::list out;
    for (auto &p : r) {
        nb::list x;
        x.append(p.first);
        x.append(p.second);
        out.append(x);
    }
    return out;
}

NB_MODULE(_pydub2, m) {
    m.def("decode", [](nb::bytes data) {
        Audio a;
        {
            // bytes cant change under us, no copy needed
            const uint8_t *buf = (const uint8_t *)data.c_str();
            size_t len = data.size();
            nb::gil_scoped_release g;
            a = decode(buf, len);
        }
        return nb::make_tuple(bytes(a.data), a.rate, a.channels, a.width);
    });

    m.def("encode", [](nb::bytes data, int rate, int channels, int width, std::string format, int bitrate) {
        Audio a = audio(data, rate, channels, width);
        std::string out;
        {
            nb::gil_scoped_release g;
            out = encode(a, format, bitrate);
        }
        return bytes(out);
    }, nb::arg("data"), nb::arg("rate"), nb::arg("channels"), nb::arg("width"), nb::arg("format"), nb::arg("bitrate") = 0);

    m.def("rms", [](nb::bytes data, int width) {
        std::string s = str(data);
        nb::gil_scoped_release g;
        return rms(s, width);
    });

    m.def("max", [](nb::bytes data, int width) {
        std::string s = str(data);
        nb::gil_scoped_release g;
        return pydub2::max(s, width);
    });

    m.def("mul", [](nb::bytes data, int width, double factor) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = mul(s, width, factor);
        }
        return bytes(s);
    });

    m.def("add", [](nb::bytes a, nb::bytes b, int width) {
        std::string x = str(a), y = str(b);
        {
            nb::gil_scoped_release g;
            x = add(x, y, width);
        }
        return bytes(x);
    });

    m.def("lin2lin", [](nb::bytes data, int width, int new_width) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = lin2lin(s, width, new_width);
        }
        return bytes(s);
    });

    m.def("tomono", [](nb::bytes data, int width, double lfac, double rfac) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = tomono(s, width, lfac, rfac);
        }
        return bytes(s);
    });

    m.def("tostereo", [](nb::bytes data, int width, double lfac, double rfac) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = tostereo(s, width, lfac, rfac);
        }
        return bytes(s);
    });

    m.def("reverse", [](nb::bytes data, int width) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = reverse(s, width);
        }
        return bytes(s);
    });

    m.def("avg", [](nb::bytes data, int width) {
        std::string s = str(data);
        nb::gil_scoped_release g;
        return avg(s, width);
    });

    m.def("bias", [](nb::bytes data, int width, int bias_) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = bias(s, width, bias_);
        }
        return bytes(s);
    });

    m.def("ratecv", [](nb::bytes data, int width, int channels, int inrate, int outrate) {
        std::string s = str(data);
        {
            nb::gil_scoped_release g;
            s = ratecv(s, width, channels, inrate, outrate);
        }
        return bytes(s);
    });

    m.def("detect_silence", [](nb::bytes data, int rate, int channels, int width, int min_silence_len,
                               double silence_thresh, int seek_step) {
        Audio a = audio(data, rate, channels, width);
        Ranges r;
        {
            nb::gil_scoped_release g;
            r = detect_silence(a, min_silence_len, silence_thresh, seek_step);
        }
        return ranges(r);
    });

    m.def("resample", [](nb::bytes data, int rate, int channels, int width, int new_rate) {
        Audio a = audio(data, rate, channels, width);
        if (new_rate < 1)
            throw std::invalid_argument("bad rate");
        {
            nb::gil_scoped_release g;
            a = resample(a, new_rate);
        }
        return bytes(a.data);
    });

    m.def("loudness", [](nb::bytes data, int rate, int channels, int width) {
        Audio a = audio(data, rate, channels, width);
        nb::gil_scoped_release g;
        return loudness(a);
    });

    m.def("detect_speech", [](nb::bytes data, int rate, int channels, int width, double threshold,
                              int min_speech_ms, int min_silence_ms) {
        Audio a = audio(data, rate, channels, width);
        Ranges r;
        {
            nb::gil_scoped_release g;
            r = detect_speech(a, threshold, min_speech_ms, min_silence_ms);
        }
        return ranges(r);
    });
}
