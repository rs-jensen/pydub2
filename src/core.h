#pragma once
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace pydub2 {

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#error samples are copied as little endian
#endif

// interleaved signed little endian pcm, the same bytes pydub keeps in _data
struct Audio {
    std::string data;
    int rate = 0;
    int channels = 0;
    int width = 0; // bytes per sample, 1 to 4 and 8 for a 64 bit wav
};

typedef std::vector<std::pair<int, int>> Ranges; // [start, end) in ms

// the long loops call this every so often, it throws to stop the work
typedef std::function<void()> Cancel;

// decode.cpp
// wav, flac, mp3, ogg vorbis and opus, a piece at a time. open throws std::runtime_error on anything else
struct Decoder {
    int rate = 0;
    int channels = 0;
    int width = 0;
    bool chained = false; // opus with more than one link, libopus starts each link fresh where ffmpeg decodes across
    uint64_t frames = 0; // what a read from the start gives, from the header for flac and opus so it can lie
    virtual ~Decoder() {}
    // up to n frames into out, returns bytes. less only at the end
    virtual size_t read(char *out, size_t n) = 0;
    virtual void seek(uint64_t frame) = 0;
};
// the memory has to outlive the decoder
std::unique_ptr<Decoder> open(const uint8_t *buf, size_t len);
std::unique_ptr<Decoder> open(const std::filesystem::path &path);

// encode.cpp
// format is wav, flac, ogg or opus. bitrate in bits per second, 0 picks a default
// throws std::invalid_argument for a format we dont have
std::string encode(const Audio &a, const std::string &format, int bitrate, const Cancel &cancel);

// pcm.cpp
// these do the same math as the old audioop module so the numbers match pydub
long long rms(const std::string &data, int width);
long long max(const std::string &data, int width);
std::string mul(const std::string &data, int width, double factor);
std::string add(const std::string &a, const std::string &b, int width);
std::string lin2lin(const std::string &data, int width, int new_width);
std::string tomono(const std::string &data, int width, double lfac, double rfac);
std::string tostereo(const std::string &data, int width, double lfac, double rfac);
std::string reverse(const std::string &data, int width);
int avg(const std::string &data, int width);
std::string bias(const std::string &data, int width, int bias);
// state is always None in pydub so there is none here
std::string ratecv(const std::string &data, int width, int channels, int inrate, int outrate);

// silence.cpp
// same walk as pydub.silence.detect_silence, thresh is in dBFS
Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step, const Cancel &cancel);

// resample.cpp
Audio resample(const Audio &a, int new_rate, const Cancel &cancel);

// loudness.cpp
// integrated loudness in LUFS (EBU R128)
double loudness(const Audio &a, const Cancel &cancel);

// vad.cpp
// silero vad. ranges where somebody is talking
Ranges detect_speech(const Audio &a, double threshold, int min_speech_ms, int min_silence_ms, const Cancel &cancel);

} // namespace pydub2
