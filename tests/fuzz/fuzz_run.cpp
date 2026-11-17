// a small mutation fuzzer for LLVMFuzzerTestOneInput where libFuzzer is missing, for gcc -fsanitize=address,undefined
// fuzz_run [-seed N] [-time S] [-runs N] [-max_len N] [-timeout S] [-alloc_mb N] [-out DIR] corpus_dir...   fuzzes
// fuzz_run file...                                                                                       runs each file once
#include <algorithm>
#include <chrono>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dirent.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);
extern "C" void __sanitizer_print_stack_trace();
extern "C" void __sanitizer_set_death_callback(void (*)());
extern "C" int __sanitizer_install_malloc_and_free_hooks(void (*)(const volatile void *, size_t), void (*)(const volatile void *));
extern "C" size_t __sanitizer_get_current_allocated_bytes();
extern "C" int __lsan_do_recoverable_leak_check();

// edge coverage from -fsanitize-coverage=trace-pc, afl style
static uint8_t cov[1 << 16];
static uintptr_t prev;
extern "C" void __sanitizer_cov_trace_pc() {
    uintptr_t pc = (uintptr_t)__builtin_return_address(0);
    uintptr_t cur = (pc ^ (pc >> 15) ^ (pc >> 31)) & 0xffff;
    cov[cur ^ prev]++;
    prev = cur >> 1;
}

static std::string cur, out_dir = ".";
static bool replay = true; // until a corpus folder is given
static uint64_t seed, iter;
static size_t alloc_limit = (size_t)160 << 20;

static void save(const char *kind) {
    if (replay) {
        return;
    }
    std::string name = out_dir + "/" + kind + "-" + std::to_string(seed) + "-" + std::to_string(iter);
    if (FILE *f = fopen(name.c_str(), "wb")) {
        fwrite(cur.data(), 1, cur.size(), f);
        fclose(f);
    }
    fprintf(stderr, "== %s: input of %zu bytes saved to %s\n", kind, cur.size(), name.c_str());
}

static void died() {
    save("crash");
}

static void hang(int) {
    fprintf(stderr, "== timeout\n");
    __sanitizer_print_stack_trace();
    save("timeout");
    _exit(70);
}

static void on_malloc(const volatile void *, size_t n) {
    static bool busy;
    if (busy || (n < alloc_limit && (n < (1 << 20) || __sanitizer_get_current_allocated_bytes() < 4 * alloc_limit))) {
        return;
    }
    busy = true;
    fprintf(stderr, "== allocation of %zu bytes, %zu in use\n", n, __sanitizer_get_current_allocated_bytes());
    __sanitizer_print_stack_trace();
    save("alloc");
    _exit(71);
}

static uint64_t rng_state;
static uint64_t rnd() { // splitmix64
    uint64_t z = (rng_state += 0x9e3779b97f4a7c15ull);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ull;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebull;
    return z ^ (z >> 31);
}
static size_t below(size_t n) {
    return n ? rnd() % n : 0;
}

static const char *dict[] = {"RIFF", "RF64", "WAVE", "fmt ", "data", "ds64", "LIST", "INFO", "fact", "fLaC", "OggS", "OpusHead",
                             "OpusTags", "\x01vorbis", "\x03vorbis", "\x05vorbis", "ID3", "Xing", "Info", "LAME", "Lavf", "APETAGEX",
                             "TAG", "\xff\xfb", "\xff\xf3", "\xff\xe3", "\xff\xf8", "\xff\xf9", "\xfe\xff"};

// offsets near the start or near a marker are where the headers are
static size_t spot(const std::string &s) {
    if (s.empty()) {
        return 0;
    }
    if (rnd() % 3) {
        return below(s.size());
    }
    if (rnd() % 2) {
        return below(std::min<size_t>(s.size(), 512));
    }
    const char *m = dict[below(sizeof(dict) / sizeof(*dict))];
    size_t at = s.find(m, below(s.size()));
    return at == std::string::npos ? below(s.size()) : std::min(s.size() - 1, at + below(64));
}

static void put_int(std::string &s, size_t at, uint64_t v, int n, bool big) {
    for (int i = 0; i < n && at + i < s.size(); i++) {
        s[at + i] = (char)(v >> (8 * (big ? n - 1 - i : i)));
    }
}

static uint32_t ogg_crc(const uint8_t *p, size_t n) {
    static uint32_t t[256];
    if (!t[1]) {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t r = i << 24;
            for (int k = 0; k < 8; k++) {
                r = r & 0x80000000u ? (r << 1) ^ 0x04c11db7u : r << 1;
            }
            t[i] = r;
        }
    }
    uint32_t c = 0;
    for (size_t i = 0; i < n; i++) {
        c = (c << 8) ^ t[(c >> 24) ^ p[i]];
    }
    return c;
}

// so a mutated page gets past the crc check
static void fix_ogg(std::string &s) {
    for (size_t at = s.find("OggS"); at != std::string::npos && at + 27 <= s.size(); at = s.find("OggS", at + 4)) {
        uint8_t *p = (uint8_t *)&s[at];
        size_t n = 27 + p[26];
        if (at + n > s.size()) {
            break;
        }
        for (int i = 0; i < p[26]; i++) {
            n += p[27 + i];
        }
        if (at + n > s.size()) {
            break;
        }
        memset(p + 22, 0, 4);
        uint32_t c = ogg_crc(p, n);
        memcpy(p + 22, &c, 4);
    }
}

static uint8_t crc8(const uint8_t *p, size_t n) {
    uint8_t c = 0;
    for (size_t i = 0; i < n; i++) {
        c ^= p[i];
        for (int k = 0; k < 8; k++) {
            c = c & 0x80 ? (uint8_t)((c << 1) ^ 7) : (uint8_t)(c << 1);
        }
    }
    return c;
}

static uint16_t crc16(const uint8_t *p, size_t n) {
    uint16_t c = 0;
    for (size_t i = 0; i < n; i++) {
        c ^= (uint16_t)(p[i] << 8);
        for (int k = 0; k < 8; k++) {
            c = c & 0x8000 ? (uint16_t)((c << 1) ^ 0x8005) : (uint16_t)(c << 1);
        }
    }
    return c;
}

// flac frames from sync to sync get a good header crc8 and frame crc16
static void fix_flac(std::string &s) {
    if (s.compare(0, 4, "fLaC")) {
        return;
    }
    uint8_t *p = (uint8_t *)&s[0];
    std::vector<size_t> sync;
    for (size_t i = 4; i + 1 < s.size(); i++) {
        if (p[i] == 0xff && (p[i + 1] & 0xfe) == 0xf8) {
            sync.push_back(i);
        }
    }
    for (size_t k = 0; k < sync.size(); k++) {
        size_t a = sync[k], e = k + 1 < sync.size() ? sync[k + 1] : s.size();
        if (e - a < 8) {
            continue;
        }
        size_t q = a + 4, ones = 0;
        while (ones < 8 && p[q] & (0x80 >> ones)) {
            ones++;
        }
        q += ones ? ones : 1;
        int bs = p[a + 2] >> 4, sr = p[a + 2] & 15;
        q += bs == 6 ? 1 : bs == 7 ? 2 : 0;
        q += sr == 12 ? 1 : sr == 13 || sr == 14 ? 2 : 0;
        if (q + 3 > e) {
            continue;
        }
        p[q] = crc8(p + a, q - a);
        uint16_t c = crc16(p + a, e - 2 - a);
        p[e - 2] = (uint8_t)(c >> 8);
        p[e - 1] = (uint8_t)c;
    }
}

static void mutate(std::string &s, const std::vector<std::string> &corpus, size_t max_len) {
    static const uint64_t vals[] = {0, 1, 2, 0x7f, 0x80, 0xff, 0x7fff, 0x8000, 0xffff, 0x10000, 0x7fffffff, 0x80000000u, 0xffffffffu,
                                    0xfffffffe, 0x7fffffffffffffffull, 0x8000000000000000ull, ~0ull, 44100, 65535, 255, 256, 4096};
    for (int n = 1 + (int)below(rnd() % 4 ? 4 : 16); n--;) {
        size_t at = spot(s);
        switch (below(13)) {
        case 0:
            if (!s.empty()) {
                s[at] ^= (char)(1 << below(8));
            }
            break;
        case 1:
            if (!s.empty()) {
                s[at] = (char)vals[below(6)];
            }
            break;
        case 2: { // a length field set to something huge, negative or small
            int w = 1 << below(4);
            uint64_t v = vals[below(sizeof(vals) / sizeof(*vals))];
            if (rnd() % 4 == 0) {
                v = s.size() + below(16) - 8;
            }
            put_int(s, at, v, w, rnd() % 2);
            break;
        }
        case 3:
            if (!s.empty()) {
                s[at] = (char)(s[at] + (int)below(35) - 17);
            }
            break;
        case 4: { // a chunk again, here or somewhere else
            size_t from = spot(s), len = below(std::min<size_t>(s.size() - from, rnd() % 4 ? 64 : 4096) + 1);
            s.insert(rnd() % 2 ? from : at, s.substr(from, len));
            break;
        }
        case 5: // cut at an offset near a header
            s.resize(rnd() % 2 ? at : std::min(s.size(), at + below(8)));
            break;
        case 6: { // splice another seed in
            const std::string &o = corpus[below(corpus.size())];
            size_t b = below(o.size() + 1);
            s = rnd() % 2 ? s.substr(0, at) + o.substr(b) : o.substr(0, b) + s.substr(at);
            break;
        }
        case 7: { // insert a range of zeros, ff or random bytes
            size_t len = 1 + below(rnd() % 4 ? 16 : 1024);
            int kind = (int)below(3);
            std::string r(len, kind == 0 ? '\0' : '\xff');
            for (size_t i = 0; kind == 2 && i < len; i++) {
                r[i] = (char)rnd();
            }
            s.insert(at, r);
            break;
        }
        case 8: // delete a range
            s.erase(at, 1 + below(rnd() % 4 ? 16 : 4096));
            break;
        case 9: { // a marker
            const char *m = dict[below(sizeof(dict) / sizeof(*dict))];
            if (rnd() % 2) {
                s.insert(at, m);
            } else {
                s.replace(at, std::min(strlen(m), s.size() - at), m);
            }
            break;
        }
        case 10: { // copy bytes over other bytes
            size_t from = spot(s), len = std::min({s.size() - from, s.size() - at, (size_t)1 + below(64)});
            s.replace(at, len, s.substr(from, len));
            break;
        }
        case 11: { // swap two pieces
            size_t b = spot(s);
            if (b < at) {
                std::swap(b, at);
            }
            size_t len = std::min(b - at, 1 + below(256));
            if (b + len <= s.size()) {
                std::string x = s.substr(at, len);
                s.replace(at, len, s.substr(b, len));
                s.replace(b, len, x);
            }
            break;
        }
        default:
            if (!s.empty()) {
                s[at] = (char)rnd();
            }
        }
    }
    if (s.size() > max_len) {
        s.resize(max_len);
    }
    if (rnd() % 4) {
        fix_ogg(s);
        fix_flac(s);
    }
}

static bool read_file(const std::string &path, std::string &s) {
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) {
        return false;
    }
    s.clear();
    char b[65536];
    for (size_t k; (k = fread(b, 1, sizeof(b), f)) > 0;) {
        s.append(b, k);
    }
    fclose(f);
    return true;
}

// new edges or a new hit count bucket
static uint8_t seen[1 << 16];
static size_t edges;
static bool novel() {
    bool any = false;
    for (size_t i = 0; i < sizeof(cov); i++) {
        if (!cov[i]) {
            continue;
        }
        uint8_t c = cov[i], b = c < 4 ? (uint8_t)(1 << (c - 1)) : c < 8 ? 8 : c < 16 ? 16 : c < 32 ? 32 : c < 128 ? 64 : 128;
        if (!seen[i]) {
            edges++;
        }
        if (b & ~seen[i]) {
            seen[i] |= b;
            any = true;
        }
    }
    return any;
}

static int timeout_s = 20;

static void one(const std::string &s) {
    cur = s;
    // ubsan halts without the death callback, so the input is on disk before it runs. a replayed one is already
    static std::string path = out_dir + "/cur-" + std::to_string(getpid());
    if (FILE *f = replay ? NULL : fopen(path.c_str(), "wb")) {
        fwrite(s.data(), 1, s.size(), f);
        fclose(f);
    }
    memset(cov, 0, sizeof(cov));
    prev = 0;
    alarm(timeout_s);
    LLVMFuzzerTestOneInput((const uint8_t *)cur.data(), cur.size());
    alarm(0);
}

int main(int argc, char **argv) {
    double secs = 60;
    uint64_t runs = UINT64_MAX;
    size_t max_len = 256 << 10;
    std::vector<std::string> dirs;
    seed = (uint64_t)time(NULL);
    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto num = [&]() { return i + 1 < argc ? strtod(argv[++i], NULL) : 0; };
        if (a == "-seed") {
            seed = strtoull(argv[++i], NULL, 10);
        } else if (a == "-time") {
            secs = num();
        } else if (a == "-runs") {
            runs = (uint64_t)num();
        } else if (a == "-max_len") {
            max_len = (size_t)num();
        } else if (a == "-timeout") {
            timeout_s = (int)num();
        } else if (a == "-alloc_mb") {
            alloc_limit = (size_t)num() << 20;
        } else if (a == "-out") {
            out_dir = argv[++i];
        } else {
            dirs.push_back(a);
        }
    }
    signal(SIGALRM, hang);
    __sanitizer_set_death_callback(died);
    __sanitizer_install_malloc_and_free_hooks(on_malloc, NULL);
    std::vector<std::string> corpus;
    for (auto &d : dirs) {
        struct stat st;
        if (stat(d.c_str(), &st) || !S_ISDIR(st.st_mode)) {
            continue;
        }
        replay = false;
        DIR *dir = opendir(d.c_str());
        std::vector<std::string> names;
        while (struct dirent *e = readdir(dir)) {
            if (e->d_name[0] != '.') {
                names.push_back(d + "/" + e->d_name);
            }
        }
        closedir(dir);
        std::sort(names.begin(), names.end()); // the same order every time, so a seed gives the same run
        for (auto &n : names) {
            std::string s;
            if (read_file(n, s) && s.size() <= max_len) {
                corpus.push_back(s);
            }
        }
    }
    if (replay) {
        for (auto &f : dirs) {
            std::string s;
            if (!read_file(f, s)) {
                fprintf(stderr, "cant read %s\n", f.c_str());
                return 1;
            }
            fprintf(stderr, "running %s (%zu bytes)\n", f.c_str(), s.size());
            one(s);
            if (__lsan_do_recoverable_leak_check()) {
                fprintf(stderr, "== leak in %s\n", f.c_str());
                return 72;
            }
        }
        fprintf(stderr, "done, no errors\n");
        return 0;
    }
    if (corpus.empty()) {
        corpus.push_back("");
    }
    fprintf(stderr, "seed %llu, %zu seeds, max_len %zu, timeout %d s, alloc limit %zu MB\n", (unsigned long long)seed,
            corpus.size(), max_len, timeout_s, alloc_limit >> 20);
    for (auto &s : corpus) {
        one(s);
        novel();
    }
    rng_state = seed;
    size_t seeds = corpus.size();
    // the last inputs, to find the one that leaked when a leak check fails
    std::vector<std::string> ring(256);
    auto t0 = std::chrono::steady_clock::now(), last = t0;
    std::string q = out_dir + "/queue";
    mkdir(q.c_str(), 0755);
    for (iter = 0; iter < runs; iter++) {
        auto now = std::chrono::steady_clock::now();
        double t = std::chrono::duration<double>(now - t0).count();
        if (t > secs) {
            break;
        }
        if (std::chrono::duration<double>(now - last).count() > 30) {
            last = now;
            fprintf(stderr, "#%llu %.0f s, %.0f exec/s, corpus %zu (+%zu), edges %zu\n", (unsigned long long)iter, t, iter / t,
                    corpus.size(), corpus.size() - seeds, edges);
        }
        std::string s = corpus[below(corpus.size())];
        mutate(s, corpus, max_len);
        auto a = std::chrono::steady_clock::now();
        one(s);
        double took = std::chrono::duration<double>(std::chrono::steady_clock::now() - a).count();
        if (took > timeout_s / 4.0) {
            fprintf(stderr, "== slow: %.1f s\n", took);
            save("slow");
        }
        ring[iter % ring.size()] = s;
        if (novel()) {
            corpus.push_back(s);
            std::string name = q + "/" + std::to_string(seed) + "-" + std::to_string(iter);
            if (FILE *f = fopen(name.c_str(), "wb")) {
                fwrite(s.data(), 1, s.size(), f);
                fclose(f);
            }
        }
        if (iter % ring.size() == ring.size() - 1 && __lsan_do_recoverable_leak_check()) {
            // a leak stays reported in this process, so each one is checked alone in a fresh one with replay
            fprintf(stderr, "== leak in one of the last %zu inputs, saved as leak-*\n", ring.size());
            for (auto &r : ring) {
                cur = r;
                iter++;
                save("leak");
            }
            return 72;
        }
    }
    double t = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    fprintf(stderr, "done: seed %llu, %llu runs in %.0f s, corpus %zu (+%zu), edges %zu\n", (unsigned long long)seed,
            (unsigned long long)iter, t, corpus.size(), corpus.size() - seeds, edges);
    return 0;
}
