# lame 3.100 refills its global quantizer tables at every lame_init_params, while encoders in other threads read them
file(READ libmp3lame/quantize_pvt.c text)
string(REPLACE
    "void\niteration_init(lame_internal_flags * gfc)\n"
    "static int tables_done; /* filled inside lame_init_params, which pydub2 never runs in two threads at once */\n\nvoid\niteration_init(lame_internal_flags * gfc)\n"
    text "${text}")
string(REPLACE
    "        pow43[0] = 0.0;\n"
    "        if (!tables_done) {\n        pow43[0] = 0.0;\n"
    text "${text}")
string(REPLACE
    "            pow20[i] = pow(2.0, (double) (i - 210 - Q_MAX2) * 0.25);\n"
    "            pow20[i] = pow(2.0, (double) (i - 210 - Q_MAX2) * 0.25);\n        tables_done = 1;\n        }\n"
    text "${text}")
file(WRITE libmp3lame/quantize_pvt.c "${text}")
string(SHA256 got "${text}")
if(NOT got STREQUAL f6674deb82a7f6840a893c712669615ef1eb63d4ec5e0579361a22dfc7c85558)
    message(FATAL_ERROR "quantize_pvt.c is not what the patch expects")
endif()
