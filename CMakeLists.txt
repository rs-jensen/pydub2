cmake_minimum_required(VERSION 3.28)
project(pydub2 C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_CXX_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
if(NOT MSVC)
    set(CMAKE_C_FLAGS_RELEASE "-O2 -DNDEBUG")
    set(CMAKE_CXX_FLAGS_RELEASE "-O2 -DNDEBUG")
endif()

# vorbis 1.3.7 still asks for cmake 2.8
set(CMAKE_POLICY_VERSION_MINIMUM 3.5)
set(BUILD_SHARED_LIBS OFF)
set(BUILD_TESTING OFF)
set(INSTALL_DOCS OFF)
set(BUILD_PROGRAMS OFF)
set(BUILD_EXAMPLES OFF)
set(BUILD_DOCS OFF)
set(BUILD_CXXLIBS OFF)
set(INSTALL_MANPAGES OFF)
set(WITH_OGG OFF)
set(LIBSAMPLERATE_EXAMPLES OFF)

include(FetchContent)
set(xiph https://downloads.xiph.org/releases)
FetchContent_Declare(ogg EXCLUDE_FROM_ALL OVERRIDE_FIND_PACKAGE
    URL ${xiph}/ogg/libogg-1.3.6.tar.gz
    URL_HASH SHA256=83e6704730683d004d20e21b8f7f55dcb3383cdf84c0daedf30bde175f774638)
FetchContent_Declare(vorbis EXCLUDE_FROM_ALL
    URL ${xiph}/vorbis/libvorbis-1.3.7.tar.gz
    URL_HASH SHA256=0e982409a9c3fc82ee06e08205b1355e5c6aa4c36bca58146ef399621b0ce5ab)
FetchContent_Declare(flac EXCLUDE_FROM_ALL
    URL ${xiph}/flac/flac-1.5.0.tar.xz
    URL_HASH SHA256=f2c1c76592a82ffff8413ba3c4a1299b6c7ab06c734dee03fd88630485c2b920)
FetchContent_Declare(opus EXCLUDE_FROM_ALL
    URL ${xiph}/opus/opus-1.6.1.tar.gz
    URL_HASH SHA256=6ffcb593207be92584df15b32466ed64bbec99109f007c82205f0194572411a1)
FetchContent_Declare(opusfile
    URL ${xiph}/opus/opusfile-0.12.tar.gz
    URL_HASH SHA256=118d8601c12dd6a44f52423e68ca9083cc9f2bfe72da7a8c1acb22a80ae3550b)
FetchContent_Declare(opusenc
    URL ${xiph}/opus/libopusenc-0.3.tar.gz
    URL_HASH SHA256=f616d3aff9b2034547894ccb8ab56c36cf1a4acb0d922c5d7119f97bbe58642c)
FetchContent_Declare(samplerate EXCLUDE_FROM_ALL
    URL https://github.com/libsndfile/libsamplerate/releases/download/0.2.2/libsamplerate-0.2.2.tar.xz
    URL_HASH SHA256=3258da280511d24b49d6b08615bbe824d0cacc9842b0e4caf11c52cf2b043893)
FetchContent_MakeAvailable(ogg vorbis flac opus opusfile opusenc samplerate)

# opusfile and libopusenc only ship autotools, so build their few files here
add_library(opusfile STATIC
    ${opusfile_SOURCE_DIR}/src/info.c
    ${opusfile_SOURCE_DIR}/src/internal.c
    ${opusfile_SOURCE_DIR}/src/opusfile.c
    ${opusfile_SOURCE_DIR}/src/stream.c)
target_include_directories(opusfile PUBLIC ${opusfile_SOURCE_DIR}/include)
target_link_libraries(opusfile PUBLIC Ogg::ogg Opus::opus)

add_library(opusenc STATIC
    ${opusenc_SOURCE_DIR}/src/ogg_packer.c
    ${opusenc_SOURCE_DIR}/src/opus_header.c
    ${opusenc_SOURCE_DIR}/src/opusenc.c
    ${opusenc_SOURCE_DIR}/src/picture.c
    ${opusenc_SOURCE_DIR}/src/resample.c
    ${opusenc_SOURCE_DIR}/src/unicode_support.c)
target_include_directories(opusenc PUBLIC ${opusenc_SOURCE_DIR}/include)
target_compile_definitions(opusenc PRIVATE OUTSIDE_SPEEX RANDOM_PREFIX=libopusenc
    PACKAGE_NAME="libopusenc" PACKAGE_VERSION="0.3")
target_link_libraries(opusenc PUBLIC Opus::opus)

add_library(ebur128 STATIC src/third_party/ebur128.c)
target_include_directories(ebur128 PUBLIC src/third_party PRIVATE src/third_party/queue)

find_package(Python 3.9 COMPONENTS Interpreter Development.Module REQUIRED)
execute_process(COMMAND ${Python_EXECUTABLE} -m nanobind --cmake_dir
    OUTPUT_VARIABLE nanobind_ROOT OUTPUT_STRIP_TRAILING_WHITESPACE)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(_pydub2 NB_STATIC
    src/bind.cpp src/pcm.cpp src/silence.cpp src/decode.cpp src/encode.cpp
    src/resample.cpp src/loudness.cpp src/vad.cpp src/third_party/stb_vorbis.c)
target_link_libraries(_pydub2 PRIVATE FLAC::FLAC vorbisenc vorbis opusfile opusenc
    SampleRate::samplerate ebur128)
if(UNIX AND NOT APPLE)
    target_link_options(_pydub2 PRIVATE -Wl,--exclude-libs,ALL)
endif()

install(TARGETS _pydub2 LIBRARY DESTINATION . COMPONENT python)
