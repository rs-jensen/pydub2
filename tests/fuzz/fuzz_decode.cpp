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

// reads up to max frames, the first calls in odd sized chunks. a short read must only come at the end
static void drain(Decoder &d, size_t chunk, uint64_t max) {
    size_t fw = (size_t)d.channels * d.width;
    std::vector<char> out(65537 * fw);
    for (uint64_t done = 0, calls = 0; done < max; calls++) {
        size_t n = calls < 100 ? chunk : 65537;
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

static void run(Decoder &d, const uint8_t *data, size_t size) {
    static const size_t odd[] = {1, 7, 1000, 4093, 65537};
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < size; i++) {
        h = (h ^ data[i]) * 1099511628211ull;
    }
    drain(d, odd[h % 5], 1 << 19);
    uint64_t at[] = {0, d.frames / 2, d.frames ? d.frames - 1 : 0, d.frames + 5, h % 100000, h >> 20, h >> 3};
    for (uint64_t to : at) {
        d.seek(to);
        drain(d, odd[(h >> 8) % 5], 1 << 16);
    }
}

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    std::unique_ptr<uint8_t[]> copy(new uint8_t[size ? size : 1]);
    memcpy(copy.get(), data, size);
    try {
        std::unique_ptr<Decoder> d = open(copy.get(), size);
        run(*d, copy.get(), size);
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
        run(*d, copy.get(), size);
    } catch (std::runtime_error &) {
    }
    return 0;
}
