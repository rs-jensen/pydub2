#include <algorithm>
#include <climits>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <string>
#include <unistd.h>
#include <vector>

#include "../../src/core.h"

using namespace pydub2;

// a read asks for at most this, like the step in bind.cpp
static const size_t most = (size_t)16 << 20;

// reads up to max frames, the first calls in odd sized chunks. a short read must only come at the end
static void drain(Decoder &d, size_t chunk, uint64_t max) {
    size_t fw = (size_t)d.channels * d.width;
    size_t big = std::max<size_t>(1, std::min<size_t>(65537, most / fw));
    std::vector<char> out(big * fw);
    for (uint64_t done = 0, calls = 0; done < max; calls++) {
        size_t n = std::min(calls < 100 ? chunk : 65537, big);
        size_t k = d.read(out.data(), n);
        if (k % fw || k > n * fw) {
            abort();
        }
        done += k / fw;
        if (k < n * fw) {
            break;
        }
    }
}

// take in bind.cpp without python: the room comes from the header when exact, the rest in pieces up to cap bytes
static std::string take(Decoder &d, int64_t n, size_t cap) {
    size_t fw = d.channels * d.width;
    uint64_t left = n >= 0 ? (uint64_t)n : UINT64_MAX;
    size_t room = (n < 0 ? (d.exact ? d.frames : 0) : std::min<uint64_t>(left, d.exact ? d.frames : ((size_t)64 << 20) / fw)) * fw;
    std::string out(room, 0);
    size_t got = 0, step = std::max<size_t>(1, most / fw);
    while (got < room) {
        size_t m = std::min(step, (room - got) / fw);
        size_t r = d.read(&out[got], m);
        got += r;
        left -= r / fw;
        if (r < m * fw) {
            break;
        }
    }
    bool full = got == room;
    out.resize(got);
    while (full && left && out.size() < cap) {
        size_t m = std::min<uint64_t>(left, std::min(step, std::max<size_t>(out.size() / fw, 4096)));
        std::string piece(m * fw, 0);
        size_t r = d.read(&piece[0], m);
        if (r % fw || r > m * fw) {
            abort();
        }
        out.append(piece, 0, r);
        left -= r / fw;
        full = r == m * fw;
    }
    return out;
}

static void run(Decoder &d, uint64_t h) {
    static const size_t odd[] = {1, 7, 1000, 4093, 65537};
    drain(d, odd[h % 5], 1 << 19);
    uint64_t at[] = {0, d.frames / 2, d.frames ? d.frames - 1 : 0, d.frames + 5, h % 100000, h >> 20, h >> 3};
    for (uint64_t to : at) {
        d.seek(to);
        drain(d, odd[(h >> 8) % 5], 1 << 16);
    }
}

// what python can hand the writers and the resampler after a load, with odd settings
static void write(const std::string &pcm, const Decoder &d, const uint8_t *data, size_t size, uint64_t h) {
    static const char *formats[] = {"wav", "flac", "ogg", "opus", "mp3"};
    static const int bitrates[] = {0, 0, 1, -1, 500, 6000, 64000, 512001, INT_MAX, INT_MIN};
    Audio a;
    a.rate = d.rate;
    a.channels = d.channels;
    a.width = d.width;
    // sometimes the same bytes as other channel counts, widths and rates
    if (h >> 40 & 1) {
        a.width = 1 + (h >> 41) % 4;
        a.channels = 1 + (h >> 43) % 9;
        a.rate = (int)(h >> 47 & 1 ? 1 + (h >> 48) % 200000 : 1 + (h >> 9) % INT_MAX);
    }
    if (a.width > 4) {
        return;
    }
    size_t fw = a.channels * a.width;
    a.data = std::string_view(pcm.data(), std::min<size_t>({pcm.size() / fw, (h >> 13) % 3000, std::max<size_t>(1, (256 << 10) / fw)}) * fw);
    Tags tags;
    for (size_t i = 0, at = 0; i < (h >> 30) % 4; i++) {
        size_t k = std::min<size_t>(size - at, (h >> (i * 4)) % 40), v = std::min<size_t>(size - at - k, (h >> (i * 5)) % 300);
        tags.push_back({std::string((const char *)data + at, k), std::string((const char *)data + at + k, v)});
        at += k + v;
    }
    std::string out;
    Alloc alloc = [&out](size_t n) {
        out.assign(n, 0);
        return &out[0];
    };
    try {
        wav_head(a);
    } catch (std::runtime_error &) {
    }
    try {
        encode(a, formats[(h >> 20) % 5], bitrates[(h >> 23) % 10], tags, [] {}, alloc);
        std::unique_ptr<Decoder> back = open((const uint8_t *)out.data(), out.size());
        drain(*back, 4093, 1 << 14);
    } catch (std::runtime_error &) {
    } catch (std::invalid_argument &) {
    }
    int64_t r = a.rate;
    int64_t rates[] = {1, 7, 8000, 44100, 48000, 192000, r - 1, r + 1, r * 256, r * 256 + 1, r / 256, r / 256 - 1, INT_MAX};
    int64_t to = rates[(h >> 27) % 13];
    // the ratio is the caller's choice, so keep the result small, libsamplerate is slow under the sanitizers
    if (to >= 1 && to <= INT_MAX && (double)a.data.size() / a.width * to / a.rate < (1 << 17)) {
        try {
            resample(a, (int)to, [] {}, alloc);
        } catch (std::runtime_error &) {
        }
    }
}

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    std::unique_ptr<uint8_t[]> copy(new uint8_t[size ? size : 1]);
    memcpy(copy.get(), data, size);
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < size; i++) {
        h = (h ^ data[i]) * 1099511628211ull;
    }
    try {
        std::unique_ptr<Decoder> d = open(copy.get(), size);
        // decode() and Decoder.read() in bind.cpp, then a load of part of the file
        std::string pcm = take(*d, -1, (size_t)32 << 20);
        write(pcm, *d, data, size, h);
        d = open(copy.get(), size);
        d->seek(h % 3 ? h % (d->frames + 2) : h >> 1);
        take(*d, h >> 61 ? (int64_t)(h >> 33) % 100000 : (int64_t)(h >> 2), (size_t)4 << 20);
        d = open(copy.get(), size);
        run(*d, h);
    } catch (std::runtime_error &) {
    }
    static std::string path = "/dev/shm/fuzz_decode_" + std::to_string(getpid());
    FILE *f = fopen(path.c_str(), "wb");
    if (!f) {
        abort();
    }
    fwrite(data, 1, size, f);
    fclose(f);
    try {
        std::unique_ptr<Decoder> d = open(path);
        run(*d, h);
    } catch (std::runtime_error &) {
    }
    remove(path.c_str());
    return 0;
}
