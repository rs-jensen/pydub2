#include <atomic>
#include <chrono>
#include <cstring>
#include <mutex>
#include <optional>
#include <vector>

#include <nanobind/nanobind.h>
#include <nanobind/stl/filesystem.h>
#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include "core.h"

namespace nb = nanobind;
using namespace pydub2;

// signals only reach the main thread, the others get a check that does nothing
static unsigned long main_thread;
static std::atomic<int> fused_state{-1};

// decided on first use by asking the audioop of the user, the platform is the guess when there is none
static bool fused() {
    int f = fused_state.load();
    if (f >= 0) {
        return f;
    }
    f = 0;
#if defined(__aarch64__) && defined(__APPLE__)
    f = 1;
#elif defined(__aarch64__)
    f = nb::cast<int>(nb::module_::import_("sys").attr("version_info")[1]) >= 13;
#endif
    try {
        nb::dict d;
        nb::module_::import_("builtins").attr("exec")(
            "import warnings\n"
            "def probe(d):\n"
            "    with warnings.catch_warnings():\n"
            "        warnings.simplefilter('ignore')\n"
            "        import audioop\n"
            "    return audioop.rms(d, 4)\n", d);
        // these differ with and without the fused multiply add
        std::string probe(28, 0);
        for (int i = 0; i < 7; i++) {
            int32_t v = 2122901605;
            memcpy(&probe[i * 4], &v, 4);
        }
        long long got = nb::cast<long long>(d["probe"](nb::bytes(probe.data(), probe.size())));
        if (got == rms(probe, 4, true)) {
            f = 1;
        } else if (got == rms(probe, 4, false)) {
            f = 0;
        }
    } catch (nb::python_error &) {
    }
    fused_state = f;
    return f;
}

// every 100 ms the long loops take the gil to let python run its signal handlers
static Cancel interrupt() {
    if (PyThread_get_thread_ident() != main_thread) {
        return [] {};
    }
    auto last = std::chrono::steady_clock::now();
    return [last]() mutable {
        auto now = std::chrono::steady_clock::now();
        if (now - last < std::chrono::milliseconds(100)) {
            return;
        }
        last = now;
        nb::gil_scoped_acquire g;
        if (PyErr_CheckSignals()) {
            throw nb::python_error();
        }
    };
}

// the caller holds the bytes and they cant change, so the core reads them in place
static std::string_view str(nb::bytes b) {
    return std::string_view(b.c_str(), b.size());
}

// bytes, or a slice of them seen through a memoryview, read in place
struct View {
    Py_buffer v{};
    explicit View(nb::handle h) {
        if (PyObject_GetBuffer(h.ptr(), &v, PyBUF_SIMPLE)) {
            throw nb::python_error();
        }
    }
    View(const View &) = delete;
    ~View() {
        PyBuffer_Release(&v);
    }
    std::string_view get() const {
        return std::string_view((const char *)v.buf, (size_t)v.len);
    }
};

static nb::object blank(size_t n) {
    nb::object out = nb::steal(PyBytes_FromStringAndSize(NULL, (Py_ssize_t)n));
    if (!out.is_valid()) {
        throw nb::python_error();
    }
    return out;
}

// the core fills the bytes python gets, no second copy
static Alloc into(nb::object &out) {
    return [&out](size_t n) {
        nb::gil_scoped_acquire g;
        out = blank(n);
        return PyBytes_AS_STRING(out.ptr());
    };
}

static Audio audio(nb::bytes data, int rate, int channels, int width) {
    if (rate < 1 || channels < 1 || channels > 65535 || width < 1 || width > 4) {
        throw std::invalid_argument("bad rate, channels or sample width");
    }
    Audio a;
    a.rate = rate;
    a.channels = (size_t)channels;
    a.width = (size_t)width;
    a.data = str(data);
    if (a.data.size() % (a.channels * a.width)) {
        throw std::invalid_argument("data is not a whole number of frames");
    }
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
    std::mutex lock{}; // seek and read from two threads
};

// n frames or all that is left, decoded straight into the bytes python gets
// the header can lie about frames, so unless d.exact it is only used for n frames and the whole file comes in pieces
static nb::bytes take(Stream &s, int64_t n) {
    Decoder &d = *s.d;
    size_t fw = d.channels * d.width;
    uint64_t left = n >= 0 ? (uint64_t)n : UINT64_MAX;
    size_t room = (n < 0 ? (d.exact ? d.frames : 0) : std::min<uint64_t>(left, d.exact ? d.frames : ((size_t)64 << 20) / fw)) * fw, got = 0;
    nb::object out = blank(room);
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
            if (r < m * fw) {
                break;
            }
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
        nb::object all = blank(got + more);
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
        .def_prop_ro("exact", [](Stream &s) { return s.d->exact; })
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

    m.def("encode", [](nb::bytes data, int rate, int channels, int width, std::string format, int bitrate,
                       std::vector<std::pair<nb::bytes, nb::bytes>> tags) {
        Audio a = audio(data, rate, channels, width);
        Tags t;
        for (auto &p : tags) {
            t.push_back({std::string(p.first.c_str(), p.first.size()), std::string(p.second.c_str(), p.second.size())});
        }
        nb::object out;
        {
            nb::gil_scoped_release g;
            encode(a, format, bitrate, t, interrupt(), into(out));
        }
        return out;
    }, nb::arg("data"), nb::arg("rate"), nb::arg("channels"), nb::arg("width"), nb::arg("format"), nb::arg("bitrate") = 0,
       nb::arg("tags") = nb::list());

    m.def("wav_head", [](nb::bytes data, int rate, int channels, int width) {
        std::string s = wav_head(audio(data, rate, channels, width));
        return nb::bytes(s.data(), s.size());
    });

    m.def("rms", [](nb::bytes data, long long width) {
        std::string_view s = str(data);
        bool f = fused();
        nb::gil_scoped_release g;
        return rms(s, width, f);
    });

    m.def("max", [](nb::bytes data, long long width) {
        std::string_view s = str(data);
        nb::gil_scoped_release g;
        return pydub2::max(s, width);
    });

    m.def("mul", [](nb::object data, long long width, double factor) {
        View v(data);
        std::string_view s = v.get();
        nb::object out;
        {
            nb::gil_scoped_release g;
            mul(s, width, factor, into(out));
        }
        return out;
    });

    m.def("lowpass", [](nb::bytes data, long long width, long long channels, double alpha) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            lowpass(s, width, channels, alpha, into(out));
        }
        return out;
    });

    m.def("highpass", [](nb::bytes data, long long width, long long channels, double alpha) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            highpass(s, width, channels, alpha, into(out));
        }
        return out;
    });

    m.def("compress", [](nb::bytes data, long long width, long long channels, double thresh, double ratio, double attack_frames,
                         double release_frames, long long look) {
        std::string_view s = str(data);
        nb::object out;
        Cancel cancel = interrupt();
        bool f = fused();
        try {
            nb::gil_scoped_release g;
            compress(s, width, channels, thresh, ratio, attack_frames, release_frames, look, f, cancel, into(out));
        } catch (std::domain_error &e) {
            // pydub divides by a threshold of 0 in python
            PyErr_SetString(PyExc_ZeroDivisionError, e.what());
            throw nb::python_error();
        }
        return out;
    });

    m.def("add", [](nb::bytes a, nb::bytes b, long long width) {
        std::string_view x = str(a), y = str(b);
        nb::object out;
        {
            nb::gil_scoped_release g;
            add(x, y, width, into(out));
        }
        return out;
    });

    m.def("mix", [](nb::object head, nb::object tail, nb::object over, long long width, size_t n, std::optional<double> gain) {
        View h(head), t(tail), o(over);
        std::string_view hs = h.get(), ts = t.get(), os = o.get();
        nb::object out;
        {
            nb::gil_scoped_release g;
            mix(hs, ts, os, width, n, gain.has_value(), gain.value_or(0), into(out));
        }
        return out;
    });

    m.def("widen24", [](nb::bytes data, long long width) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            widen24(s, width, into(out));
        }
        return out;
    });

    m.def("channel", [](nb::bytes data, long long width, long long channels, long long index) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            channel(s, width, channels, index, into(out));
        }
        return out;
    });

    m.def("interleave", [](nb::list parts, long long width, size_t frames) {
        // held here, another thread can take them out of the list while the gil is released
        std::vector<nb::bytes> held;
        std::vector<std::string_view> views;
        for (nb::handle h : parts) {
            held.push_back(nb::cast<nb::bytes>(h));
            views.push_back(str(held.back()));
        }
        nb::object out;
        {
            nb::gil_scoped_release g;
            interleave(views, width, frames, into(out));
        }
        return out;
    });

    m.def("lin2lin", [](nb::bytes data, long long width, long long new_width) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            lin2lin(s, width, new_width, into(out));
        }
        return out;
    });

    m.def("tomono", [](nb::bytes data, long long width, double lfac, double rfac) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            tomono(s, width, lfac, rfac, into(out));
        }
        return out;
    });

    m.def("tostereo", [](nb::bytes data, long long width, double lfac, double rfac) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            tostereo(s, width, lfac, rfac, into(out));
        }
        return out;
    });

    m.def("downmix", [](nb::bytes data, long long width, long long channels) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            downmix(s, width, channels, into(out));
        }
        return out;
    });

    m.def("reverse", [](nb::bytes data, long long width) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            reverse(s, width, into(out));
        }
        return out;
    });

    m.def("avg", [](nb::bytes data, long long width, long long step, long long first) {
        if (step < 1 || first < 0) {
            throw std::invalid_argument("step should be >= 1 and first >= 0");
        }
        std::string_view s = str(data);
        nb::gil_scoped_release g;
        return avg(s, width, step, first);
    }, nb::arg("data"), nb::arg("width"), nb::arg("step") = 1, nb::arg("first") = 0);

    m.def("bias", [](nb::bytes data, long long width, int bias_) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            bias(s, width, bias_, into(out));
        }
        return out;
    });

    m.def("bias_pairs", [](nb::bytes data, long long width, int even, int odd) {
        std::string_view s = str(data);
        nb::object out;
        {
            nb::gil_scoped_release g;
            bias_pairs(s, width, even, odd, into(out));
        }
        return out;
    });

    m.def("ratecv", [](nb::bytes data, long long width, long long channels, int inrate, int outrate) {
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
        bool f = fused();
        {
            nb::gil_scoped_release g;
            r = detect_silence(a, min_silence_len, silence_thresh, seek_step, f, interrupt());
        }
        return ranges(r);
    });

    m.def("resample", [](nb::bytes data, int rate, int channels, int width, int new_rate) {
        Audio a = audio(data, rate, channels, width);
        if (new_rate < 1) {
            throw std::invalid_argument("bad rate");
        }
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
