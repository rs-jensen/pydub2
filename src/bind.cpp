#include <chrono>
#include <mutex>
#include <vector>

#include <nanobind/nanobind.h>
#include <nanobind/stl/filesystem.h>
#include <nanobind/stl/string.h>

#include "core.h"

namespace nb = nanobind;
using namespace pydub2;

// signals only reach the main thread, the others get a check that does nothing
static unsigned long main_thread;

// every 100 ms the long loops take the gil to let python run its signal handlers
static Cancel interrupt() {
    if (PyThread_get_thread_ident() != main_thread)
        return [] {};
    auto last = std::chrono::steady_clock::now();
    return [last]() mutable {
        auto now = std::chrono::steady_clock::now();
        if (now - last < std::chrono::milliseconds(100))
            return;
        last = now;
        nb::gil_scoped_acquire g;
        if (PyErr_CheckSignals())
            throw nb::python_error();
    };
}

// the caller holds the bytes and they cant change, so the core reads them in place
static std::string_view str(nb::bytes b) {
    return std::string_view(b.c_str(), b.size());
}

// the core fills the bytes python gets, no second copy
static Alloc into(nb::object &out) {
    return [&out](size_t n) {
        nb::gil_scoped_acquire g;
        out = nb::steal(PyBytes_FromStringAndSize(NULL, n));
        if (!out.is_valid())
            throw nb::python_error();
        return PyBytes_AS_STRING(out.ptr());
    };
}

static Audio audio(nb::bytes data, int rate, int channels, int width) {
    if (rate < 1 || channels < 1 || channels > 65535 || width < 1 || width > 4)
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
// unless the decoder counted them itself
static nb::bytes take(Stream &s, int64_t n) {
    Decoder &d = *s.d;
    size_t fw = (size_t)d.channels * d.width;
    uint64_t left = n >= 0 ? (uint64_t)n : UINT64_MAX;
    size_t room = std::min<uint64_t>(n >= 0 ? left : d.frames, n < 0 && d.exact ? UINT64_MAX : ((size_t)64 << 20) / fw) * fw, got = 0;
    nb::object out = nb::steal(PyBytes_FromStringAndSize(NULL, room));
    if (!out.is_valid()) {
        throw nb::python_error();
    }
    std::vector<std::string> rest; // pieces, one string that grows would copy itself and hold twice the memory
    size_t more = 0;
    {
        char *p = PyBytes_AS_STRING(out.ptr());
        Cancel cancel = interrupt();
        nb::gil_scoped_release g;
        std::lock_guard<std::mutex> hold(s.lock);
        size_t step = std::max<size_t>(1, ((size_t)16 << 20) / fw);
        while (got < room) {
            cancel();
            size_t m = std::min(step, (room - got) / fw);
            size_t r = d.read(p + got, m);
            got += r;
            left -= r / fw;
            if (r < m * fw)
                break;
        }
        bool full = got == room;
        while (full && left) {
            cancel();
            // grow with what came, a full step of zeros for a few frames is 16 MB of rss
            size_t m = std::min<uint64_t>(left, std::min(step, std::max<size_t>(more / fw, 4096)));
            std::string piece(m * fw, 0);
            size_t r = d.read(&piece[0], m);
            piece.resize(r);
            more += r;
            rest.push_back(std::move(piece));
            left -= r / fw;
            full = r == m * fw;
        }
    }
    if (got < room || more) {
        nb::object all = nb::steal(PyBytes_FromStringAndSize(NULL, got + more));
        if (!all.is_valid()) {
            throw nb::python_error();
        }
        memcpy(PyBytes_AS_STRING(all.ptr()), PyBytes_AS_STRING(out.ptr()), got);
        out = all;
        // let go of each piece as it is copied to keep the peak down
        char *q = PyBytes_AS_STRING(out.ptr()) + got;
        for (auto &piece : rest) {
            memcpy(q, piece.data(), piece.size());
            q += piece.size();
            std::string().swap(piece);
        }
    }
    return nb::borrow<nb::bytes>(out);
}

NB_MODULE(_pydub2, m) {
    main_thread = nb::cast<unsigned long>(nb::module_::import_("threading").attr("main_thread")().attr("ident"));

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
        .def_prop_ro("chained", [](Stream &s) { return s.d->chained; })
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
        nb::object out;
        {
            nb::gil_scoped_release g;
            encode(a, format, bitrate, interrupt(), into(out));
        }
        return out;
    }, nb::arg("data"), nb::arg("rate"), nb::arg("channels"), nb::arg("width"), nb::arg("format"), nb::arg("bitrate") = 0);

    m.def("wav_head", [](nb::bytes data, int rate, int channels, int width) {
        std::string s = wav_head(audio(data, rate, channels, width));
        return nb::bytes(s.data(), s.size());
    });

    m.def("rms", [](nb::bytes data, int width) {
        std::string_view s = str(data);
        nb::gil_scoped_release g;
        return rms(s, width);
    });

    m.def("max", [](nb::bytes data, int width) {
        std::string_view s = str(data);
        nb::gil_scoped_release g;
        return pydub2::max(s, width);
    });

    m.def("mul", [](nb::bytes data, int width, double factor) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            mul(s, width, factor, into(out));
        }
        return out;
    });

    m.def("lowpass", [](nb::bytes data, int width, int channels, double alpha) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            lowpass(s, width, channels, alpha, into(out));
        }
        return out;
    });

    m.def("highpass", [](nb::bytes data, int width, int channels, double alpha) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            highpass(s, width, channels, alpha, into(out));
        }
        return out;
    });

    m.def("compress", [](nb::bytes data, int width, int channels, double thresh, double ratio, double attack_frames,
                         double release_frames, long long look) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            compress(s, width, channels, thresh, ratio, attack_frames, release_frames, look, into(out));
        }
        return out;
    });

    m.def("add", [](nb::bytes a, nb::bytes b, int width) {
        std::string_view x = str(a), y = str(b);
        nb::object out;
        {
            nb::gil_scoped_release g;
            add(x, y, width, into(out));
        }
        return out;
    });

    m.def("lin2lin", [](nb::bytes data, int width, int new_width) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            lin2lin(s, width, new_width, into(out));
        }
        return out;
    });

    m.def("tomono", [](nb::bytes data, int width, double lfac, double rfac) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            tomono(s, width, lfac, rfac, into(out));
        }
        return out;
    });

    m.def("tostereo", [](nb::bytes data, int width, double lfac, double rfac) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            tostereo(s, width, lfac, rfac, into(out));
        }
        return out;
    });

    m.def("reverse", [](nb::bytes data, int width) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            reverse(s, width, into(out));
        }
        return out;
    });

    m.def("avg", [](nb::bytes data, int width) {
        std::string_view s = str(data);
        nb::gil_scoped_release g;
        return avg(s, width);
    });

    m.def("bias", [](nb::bytes data, int width, int bias_) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            bias(s, width, bias_, into(out));
        }
        return out;
    });

    m.def("ratecv", [](nb::bytes data, int width, int channels, int inrate, int outrate) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            ratecv(s, width, channels, inrate, outrate, into(out));
        }
        return out;
    });

    m.def("detect_silence", [](nb::bytes data, int rate, int channels, int width, int min_silence_len,
                               double silence_thresh, int seek_step) {
        Audio a = audio(data, rate, channels, width);
        Ranges r;
        {
            nb::gil_scoped_release g;
            r = detect_silence(a, min_silence_len, silence_thresh, seek_step, interrupt());
        }
        return ranges(r);
    });

    m.def("resample", [](nb::bytes data, int rate, int channels, int width, int new_rate) {
        Audio a = audio(data, rate, channels, width);
        if (new_rate < 1)
            throw std::invalid_argument("bad rate");
        nb::object out;
        {
            nb::gil_scoped_release g;
            resample(a, new_rate, interrupt(), into(out));
        }
        return out;
    });

    m.def("loudness", [](nb::bytes data, int rate, int channels, int width) {
        Audio a = audio(data, rate, channels, width);
        nb::gil_scoped_release g;
        return loudness(a, interrupt());
    });

    m.def("detect_speech", [](nb::bytes data, int rate, int channels, int width, double threshold,
                              int min_speech_ms, int min_silence_ms) {
        Audio a = audio(data, rate, channels, width);
        Ranges r;
        {
            nb::gil_scoped_release g;
            r = detect_speech(a, threshold, min_speech_ms, min_silence_ms, interrupt());
        }
        return ranges(r);
    });
}
