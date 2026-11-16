# opusfile 0.12 dereferences NULL when ogg_sync_buffer fails (CVE-2022-47021), fix is xiph/opusfile 0a4cd796
file(READ src/opusfile.c text)
string(REPLACE
    "  buffer=(unsigned char *)ogg_sync_buffer(&_of->oy,_nbytes);\n"
    "  buffer=(unsigned char *)ogg_sync_buffer(&_of->oy,_nbytes);\n  if(OP_UNLIKELY(buffer==NULL))return OP_EFAULT;\n"
    text "${text}")
string(REPLACE
    "    buffer=ogg_sync_buffer(&_of->oy,(long)_initial_bytes);\n"
    "    buffer=ogg_sync_buffer(&_of->oy,(long)_initial_bytes);\n    if(OP_UNLIKELY(buffer==NULL))return OP_EFAULT;\n"
    text "${text}")
file(WRITE src/opusfile.c "${text}")
file(SHA256 src/opusfile.c got)
if(NOT got STREQUAL 8e0e4653af41b664b47820dd01465f5c4c5b7e5f3fa57bb4e62baa3388addee2)
    message(FATAL_ERROR "opusfile.c is not what the patch expects")
endif()
