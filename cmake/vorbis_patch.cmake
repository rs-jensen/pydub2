# libvorbis 1.3.7 shifts negative ints left and reads one float past a noise curve, fixes are xiph/vorbis 2d79800b and 315da9cc
# each file must hash as 1.3.7 or as patched, so a second run on the same sources changes nothing
file(READ lib/psy.c text)
string(REPLACE "\r\n" "\n" text "${text}") # file(WRITE) makes CRLF on windows
string(SHA256 got "${text}")
if(got STREQUAL e8ccec1fba7d00961d5c67fec30b488c912f0ad7842ec7b1aeab987b97744b6e)
    string(REPLACE
        "    p->bark[i]=((lo-1)<<16)+(hi-1);\n"
        "    p->bark[i]=((lo-1)*(1<<16))+(hi-1);\n"
        text "${text}")
    string(REPLACE
        "    inthalfoc=(int)halfoc;\n"
        "    inthalfoc=(int)halfoc;\n    if(inthalfoc>=P_BANDS-2)inthalfoc=P_BANDS-2;\n"
        text "${text}")
    file(WRITE lib/psy.c "${text}")
    string(SHA256 got "${text}")
endif()
if(NOT got STREQUAL be95cec9de85e53677eea4b720c123e9ed87700fa638cb47f4031900a7056546)
    message(FATAL_ERROR "psy.c is not what the patch expects")
endif()

file(READ lib/floor1.c text)
string(REPLACE "\r\n" "\n" text "${text}")
string(SHA256 got "${text}")
if(got STREQUAL fd4f403b56bf5538d1cc33fcf9236bca230553c143273fe17f5846ed928b6a64)
    string(REPLACE
        "            val=-1-(val<<1);\n"
        "            val=-1-(val*2);\n"
        text "${text}")
    file(WRITE lib/floor1.c "${text}")
    string(SHA256 got "${text}")
endif()
if(NOT got STREQUAL 7d0c893ad4709bd5ae5bf2e041224363787d3944ab35629a2fff80fc9ff3bd9b)
    message(FATAL_ERROR "floor1.c is not what the patch expects")
endif()
