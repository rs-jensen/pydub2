import re
import struct
import sys
import zipfile

# the vc runtime and the ucrt forwarder are not on a machine without the redistributable
BAD = re.compile(r"^(msvcp|vcruntime|msvcr|vccorlib|concrt|vcomp|ucrtbase)", re.I)


def imports(b):
    pe = struct.unpack_from("<I", b, 0x3c)[0]
    nsec, nopt = struct.unpack_from("<H", b, pe + 6)[0], struct.unpack_from("<H", b, pe + 20)[0]
    opt = pe + 24
    dirs = opt + (112 if struct.unpack_from("<H", b, opt)[0] == 0x20b else 96)
    secs = [struct.unpack_from("<IIII", b, opt + nopt + 40 * i + 8) for i in range(nsec)]

    def off(rva):
        for size, va, raw, ptr in secs:
            if va <= rva < va + max(size, raw):
                return rva - va + ptr

    def names(index, step, at):
        rva = struct.unpack_from("<I", b, dirs + 8 * index)[0]
        o = off(rva) if rva else None
        while o is not None and any(b[o:o + step]):
            n = off(struct.unpack_from("<I", b, o + at)[0])
            yield b[n:b.index(b"\0", n)].decode()
            o += step

    return list(names(1, 20, 12)) + list(names(13, 32, 4))


bad = 0
for wheel in sys.argv[1:]:
    with zipfile.ZipFile(wheel) as z:
        for name in z.namelist():
            if name.endswith((".pyd", ".dll")):
                deps = imports(z.read(name))
                print(wheel.rsplit("/", 1)[-1], name, sorted(deps, key=str.lower))
                bad += sum(1 for d in deps if BAD.match(d))
sys.exit("a dll depends on the vc runtime" if bad else 0)
