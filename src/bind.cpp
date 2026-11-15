#include <mutex>

#include <nanobind/nanobind.h>
#include <nanobind/stl/filesystem.h>
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
    if (rate < 1 || channels < 1 || width < 1 || width > 4)
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

struct Stream {
    nb::bytes keep; // what the decoder reads from, if it is memory
    std::unique_ptr<Decoder> d;
    std::mutex lock; // seek and read from two threads
};

// n frames or all that is left, decoded straight into the bytes python gets
// frames comes from the header and can lie, so only the first 64 MB are sized from it, the rest grows with what is decoded
static nb::bytes take(Stream &s, int64_t n) {
    Decoder &d = *s.d;
    size_t fw = (size_t)d.channels * d.width;
    uint64_t left = n >= 0 ? (uint64_t)n : UINT64_MAX;
    size_t room = std::min<uint64_t>(n >= 0 ? left : d.frames, ((size_t)64 << 20) / fw) * fw, got = 0;
    nb::object out = nb::steal(PyBytes_FromStringAndSize(NULL, room));
    if (!out.is_valid()) {
        throw nb::python_error();
    }
    std::string rest;
    {
        char *p = PyBytes_AS_STRING(out.ptr());
        nb::gil_scoped_release g;
        std::lock_guard<std::mutex> hold(s.lock);
        got = d.read(p, room / fw);
        left -= got / fw;
        size_t step = std::max<size_t>(1, ((size_t)16 << 20) / fw);
        bool full = got == room;
        while (full && left) {
            size_t k = rest.size(), m = std::min<uint64_t>(left, step);
            rest.resize(k + m * fw);
            size_t r = d.read(&rest[k], m);
            rest.resize(k + r);
            left -= r / fw;
            full = r == m * fw;
        }
    }
    if (got < room || rest.size()) {
        nb::object all = nb::steal(PyBytes_FromStringAndSize(NULL, got + rest.size()));
        if (!all.is_valid()) {
            throw nb::python_error();
        }
        memcpy(PyBytes_AS_STRING(all.ptr()), PyBytes_AS_STRING(out.ptr()), got);
        memcpy(PyBytes_AS_STRING(all.ptr()) + got, rest.data(), rest.size());
        out = all;
    }
    return nb::borrow<nb::bytes>(out);
}

NB_MODULE(_pydub2, m) {
    nb::class_<Stream>(m, "Decoder")
        .def("__init__", [](Stream *s, nb::bytes data) {
            std::unique_ptr<Decoder> d;
            {
                // bytes cant change under us, no copy needed
                const uint8_t *buf = (const uint8_t *)data.c_str();
                size_t len = data.size();
                nb::gil_scoped_release g;
                d = open(buf, len);
            }
            new (s) Stream{data, std::move(d)};
        })
        .def("__init__", [](Stream *s, std::filesystem::path path) {
            std::unique_ptr<Decoder> d;
            {
                nb::gil_scoped_release g;
                d = open(path);
            }
            new (s) Stream{nb::bytes(), std::move(d)};
        })
        .def_prop_ro("rate", [](Stream &s) { return s.d->rate; })
        .def_prop_ro("channels", [](Stream &s) { return s.d->channels; })
        .def_prop_ro("width", [](Stream &s) { return s.d->width; })
        .def_prop_ro("frames", [](Stream &s) { return s.d->frames; })
        .def("seek", [](Stream &s, uint64_t frame) {
            nb::gil_scoped_release g;
            std::lock_guard<std::mutex> hold(s.lock);
            s.d->seek(frame);
        })
        .def("read", &take, nb::arg("n") = -1);

    m.def("decode", [](nb::bytes data) {
        std::unique_ptr<Decoder> d;
        {
            const uint8_t *buf = (const uint8_t *)data.c_str();
            size_t len = data.size();
            nb::gil_scoped_release g;
            d = open(buf, len);
        }
        Stream s{data, std::move(d)};
        return nb::make_tuple(take(s, -1), s.d->rate, s.d->channels, s.d->width);
    });

    m.def("encode", [](nb::bytes data, int rate, int channels, int width, std::string format, int bitrate) {
        Audio a = audio(data, rate, channels, width);
        // libopusenc corrupts the heap when it resamples up from below about 90 hz
        if (format == "opus" && rate < 100)
            throw std::runtime_error("cant encode this");
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
