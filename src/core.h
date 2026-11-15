#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace pydub2 {

// interleaved signed little endian pcm, the same bytes pydub keeps in _data
struct Audio {
    std::string data;
    int rate = 0;
    int channels = 0;
    int width = 0; // bytes per sample, 1 2 or 4
};

typedef std::vector<std::pair<int, int>> Ranges; // [start, end) in ms

// decode.cpp
// wav, flac, mp3, ogg vorbis and opus. throws std::runtime_error on anything else
Audio decode(const uint8_t *buf, size_t len);

// encode.cpp
// format is wav, flac, ogg or opus. bitrate in bits per second, 0 picks a default
// throws std::invalid_argument for a format we dont have
std::string encode(const Audio &a, const std::string &format, int bitrate);

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
Ranges detect_silence(const Audio &a, int min_silence_len, double silence_thresh, int seek_step);

// resample.cpp
Audio resample(const Audio &a, int new_rate);

// loudness.cpp
// integrated loudness in LUFS (EBU R128)
double loudness(const Audio &a);

// vad.cpp
// silero vad. ranges where somebody is talking
Ranges detect_speech(const Audio &a, double threshold, int min_speech_ms, int min_silence_ms);

} // namespace pydub2
