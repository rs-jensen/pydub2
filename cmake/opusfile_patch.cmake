# the file must hash as 0.12 or as patched, so a second run on the same sources changes nothing
file(READ src/opusfile.c text)
string(REPLACE "\r\n" "\n" text "${text}") # file(WRITE) makes CRLF on windows
string(SHA256 got "${text}")
if(got STREQUAL b75f36104b255e2d4ebf5173205fef49c3978da5da6d0782657beb094eeaba8b)
    # opusfile 0.12 dereferences NULL when ogg_sync_buffer fails (CVE-2022-47021), fix is xiph/opusfile 0a4cd796
    string(REPLACE
        "  buffer=(unsigned char *)ogg_sync_buffer(&_of->oy,_nbytes);\n"
        "  buffer=(unsigned char *)ogg_sync_buffer(&_of->oy,_nbytes);\n  if(OP_UNLIKELY(buffer==NULL))return OP_EFAULT;\n"
        text "${text}")
    string(REPLACE
        "    buffer=ogg_sync_buffer(&_of->oy,(long)_initial_bytes);\n"
        "    buffer=ogg_sync_buffer(&_of->oy,(long)_initial_bytes);\n    if(OP_UNLIKELY(buffer==NULL))return OP_EFAULT;\n"
        text "${text}")
    # a failed open leaks the tags of the link it was reading, fix is from xiph/opusfile master
    string(REPLACE
        "    if(OP_UNLIKELY(ret<0))return ret;\n    links[nlinks].offset=next;\n"
        "    if(OP_UNLIKELY(ret<0))return ret;\n    /*Mark the current link count so it can be cleaned up on error.*/\n    _of->nlinks=nlinks+1;\n    links[nlinks].offset=next;\n"
        text "${text}")
    string(REPLACE
        "    /*Mark the current link count so it can be cleaned up on error.*/\n    _of->nlinks=++nlinks;\n"
        "    ++nlinks;\n"
        text "${text}")
    file(WRITE src/opusfile.c "${text}")
    string(SHA256 got "${text}")
endif()
if(NOT got STREQUAL cfc38ac4035f46422c67575881e1a73f21fa59d9995ba1563c331be51017c8fb)
    message(FATAL_ERROR "opusfile.c is not what the patch expects")
endif()
