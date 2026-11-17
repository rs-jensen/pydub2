cmake_minimum_required(VERSION 3.28)
project(pydub2_mp3 C)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
if(MSVC)
    # static runtime, the wheel then needs no vcruntime
    set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
else()
    # same as lame's own configure
    set(CMAKE_C_FLAGS_RELEASE "-O3 -DNDEBUG -fstack-protector-strong -U_FORTIFY_SOURCE -D_FORTIFY_SOURCE=2")
endif()

include(FetchContent)
FetchContent_Declare(lame
    URL https://downloads.sourceforge.net/project/lame/lame/3.100/lame-3.100.tar.gz
    URL_HASH SHA256=ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e)
FetchContent_MakeAvailable(lame)

# lame only ships autotools, this is the part of config.h the encoder needs
file(WRITE ${CMAKE_BINARY_DIR}/config.h [[
#define STDC_HEADERS 1
#define HAVE_STDINT_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRING_H 1
#define HAVE_ERRNO_H 1
#define HAVE_FCNTL_H 1
#define HAVE_LIMITS_H 1
/* lame's configure turns these on for x86, so the libmp3lame in ffmpeg has them there */
#if defined(__x86_64__) || defined(_M_X64) || defined(__i386__) || defined(_M_IX86)
#define TAKEHIRO_IEEE754_HACK 1
#define USE_FAST_LOG 1
#endif
#include <stdint.h>
typedef float ieee754_float32_t;
typedef double ieee754_float64_t;
typedef long double ieee854_float80_t;
]])

set(src ${lame_SOURCE_DIR}/libmp3lame)
add_library(mp3lame SHARED
    ${src}/VbrTag.c ${src}/bitstream.c ${src}/encoder.c ${src}/fft.c ${src}/gain_analysis.c
    ${src}/id3tag.c ${src}/lame.c ${src}/newmdct.c ${src}/presets.c ${src}/psymodel.c
    ${src}/quantize.c ${src}/quantize_pvt.c ${src}/reservoir.c ${src}/set_get.c
    ${src}/tables.c ${src}/takehiro.c ${src}/util.c ${src}/vbrquantize.c ${src}/version.c)
target_include_directories(mp3lame PRIVATE ${CMAKE_BINARY_DIR} ${lame_SOURCE_DIR}/include ${src})
target_compile_definitions(mp3lame PRIVATE HAVE_CONFIG_H _CRT_SECURE_NO_WARNINGS)
set_target_properties(mp3lame PROPERTIES PREFIX "" OUTPUT_NAME libmp3lame WINDOWS_EXPORT_ALL_SYMBOLS ON)
if(NOT WIN32)
    target_link_libraries(mp3lame PRIVATE m)
endif()
if(MSVC AND CMAKE_SYSTEM_PROCESSOR MATCHES "AMD64")
    target_sources(mp3lame PRIVATE fma3.c)
endif()
install(TARGETS mp3lame LIBRARY DESTINATION pydub2_mp3 RUNTIME DESTINATION pydub2_mp3)
