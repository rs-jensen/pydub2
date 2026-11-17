/* the static CRT takes FMA3 math paths on CPUs that have them, the mingw lame in ffmpeg does not */
#include <windows.h>
#include <math.h>

BOOL WINAPI DllMain(HINSTANCE dll, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_ATTACH)
        _set_FMA3_enable(0);
    return TRUE;
}
