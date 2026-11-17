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
    size_t channels = 0;
    size_t width = 0; // bytes per sample, 1 to 4 and 8 for a 64 bit wav
};

typedef std::vector<std::pair<int, int>> Ranges; // [start, end) in ms

typedef std::vector<std::pair<std::string, std::string>> Tags; // vorbis comments, key and value as bytes

// the long loops call this every so often, it throws to stop the work
typedef std::function<void()> Cancel;

// the result goes into memory the caller hands out, n bytes, so nothing is copied again
typedef std::function<char *(size_t)> Alloc;

// decode.cpp
// wav, flac, mp3, ogg vorbis and opus, a piece at a time. open throws std::runtime_error on anything else
struct Decoder {
    int rate = 0;
    size_t channels = 0;
    size_t width = 0;
    bool chained = false; // opus with more than one link, libopus starts each link fresh where ffmpeg decodes across
    uint64_t frames = 0; // what a read from the start gives, from the header for flac and opus so it can lie
    bool exact = false; // frames is counted or fits the file size, a reader can size a buffer from it
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
// tags are written as vorbis comments to flac, ogg and opus, wav has none
// the 44 bytes in front of the samples, so a writer can send the samples as they are
std::string wav_head(const Audio &a);
void encode(const Audio &a, const std::string &format, int bitrate, const Tags &tags, const Cancel &cancel,
            const Alloc &alloc);

// pcm.cpp
// these do the same math as the old audioop module so the numbers match pydub
// fused is audioops fused multiply add in the sum of squares, only the arm64 builds have it
double sumsq(std::string_view data, size_t width, bool fused);
long long rms(std::string_view data, size_t width, bool fused);
long long max(std::string_view data, size_t width);
void mul(std::string_view data, size_t width, double factor, const Alloc &alloc);
void add(std::string_view a, std::string_view b, size_t width, const Alloc &alloc);
void lin2lin(std::string_view data, size_t width, size_t new_width, const Alloc &alloc);
void tomono(std::string_view data, size_t width, double lfac, double rfac, const Alloc &alloc);
void tostereo(std::string_view data, size_t width, double lfac, double rfac, const Alloc &alloc);
// pydubs set_channels(1) from 3 or more channels: each channel floor divided by the count, added up in the sample type
// throws std::overflow_error where the python array would
void downmix(std::string_view data, size_t width, size_t channels, const Alloc &alloc);
void reverse(std::string_view data, size_t width, const Alloc &alloc);
int avg(std::string_view data, size_t width);
void bias(std::string_view data, size_t width, int bias, const Alloc &alloc);
// the loops of the low pass, high pass and compress_dynamic_range effects, alpha and the rest as pydub works them out
void lowpass(std::string_view data, size_t width, size_t channels, double alpha, const Alloc &alloc);
void highpass(std::string_view data, size_t width, size_t channels, double alpha, const Alloc &alloc);
void compress(std::string_view data, size_t width, size_t channels, double thresh, double ratio, double attack_frames,
              double release_frames, long long look, bool fused, const Cancel &cancel, const Alloc &alloc);
// state is always None in pydub so there is none here
void ratecv(std::string_view data, size_t width, size_t channels, int inrate, int outrate, const Alloc &alloc);

// silence.cpp
// same walk as pydub.silence.detect_silence, thresh is in dBFS
Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step, bool fused, const Cancel &cancel);

// resample.cpp
void resample(const Audio &a, int new_rate, const Cancel &cancel, const Alloc &alloc);
// the same result, handed over in pieces that are only good until the call returns
typedef std::function<void(std::string_view)> Piece;
void resample_each(const Audio &a, int new_rate, const Cancel &cancel, const Piece &emit);

// stretch.cpp
// wsola time stretch, rate 2 is twice as fast and the pitch stays. all channels move by the same offset
void stretch(const Audio &a, double rate, const Cancel &cancel, const Alloc &alloc);

// loudness.cpp
// integrated loudness in LUFS (EBU R128)
double loudness(const Audio &a, const Cancel &cancel);

// vad.cpp
// silero vad. ranges where somebody is talking
Ranges detect_speech(const Audio &a, double threshold, int min_speech_ms, int min_silence_ms, const Cancel &cancel);

} // namespace pydub2
