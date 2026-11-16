#pragma once
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace pydub2 {

#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
#error samples are copied as little endian
#endif

// interleaved signed little endian pcm, the same bytes pydub keeps in _data
struct Audio {
    std::string_view data; // not owned
    int rate = 0;
    int channels = 0;
    int width = 0; // bytes per sample, 1 to 4
};

typedef std::vector<std::pair<int, int>> Ranges; // [start, end) in ms

// the long loops call this every so often, it throws to stop the work
typedef std::function<void()> Cancel;

// the result goes into memory the caller hands out, n bytes, so nothing is copied again
typedef std::function<char *(size_t)> Alloc;

// decode.cpp
// wav, flac, mp3, ogg vorbis and opus, a piece at a time. open throws std::runtime_error on anything else
struct Decoder {
    int rate = 0;
    int channels = 0;
    int width = 0;
    uint64_t frames = 0; // what a read from the start gives, from the header for flac and opus so it can lie
    bool exact = false; // frames is counted from the file, a reader can size a buffer from it
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
// the 44 bytes in front of the samples, so a writer can send the samples as they are
std::string wav_head(const Audio &a);
void encode(const Audio &a, const std::string &format, int bitrate, const Cancel &cancel, const Alloc &alloc);

// pcm.cpp
// these do the same math as the old audioop module so the numbers match pydub
long long rms(std::string_view data, int width);
long long max(std::string_view data, int width);
void mul(std::string_view data, int width, double factor, const Alloc &alloc);
void add(std::string_view a, std::string_view b, int width, const Alloc &alloc);
void lin2lin(std::string_view data, int width, int new_width, const Alloc &alloc);
void tomono(std::string_view data, int width, double lfac, double rfac, const Alloc &alloc);
void tostereo(std::string_view data, int width, double lfac, double rfac, const Alloc &alloc);
void reverse(std::string_view data, int width, const Alloc &alloc);
int avg(std::string_view data, int width);
void bias(std::string_view data, int width, int bias, const Alloc &alloc);
// the loops of the low pass, high pass and compress_dynamic_range effects, alpha and the rest as pydub works them out
void lowpass(std::string_view data, int width, int channels, double alpha, const Alloc &alloc);
void highpass(std::string_view data, int width, int channels, double alpha, const Alloc &alloc);
void compress(std::string_view data, int width, int channels, double thresh, double ratio, double attack_frames,
              double release_frames, long long look, const Alloc &alloc);
// state is always None in pydub so there is none here
void ratecv(std::string_view data, int width, int channels, int inrate, int outrate, const Alloc &alloc);

// silence.cpp
// same walk as pydub.silence.detect_silence, thresh is in dBFS
Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step, const Cancel &cancel);

// resample.cpp
void resample(const Audio &a, int new_rate, const Cancel &cancel, const Alloc &alloc);

// loudness.cpp
// integrated loudness in LUFS (EBU R128)
double loudness(const Audio &a, const Cancel &cancel);

// vad.cpp
// silero vad. ranges where somebody is talking
Ranges detect_speech(const Audio &a, double threshold, int min_speech_ms, int min_silence_ms, const Cancel &cancel);

} // namespace pydub2
