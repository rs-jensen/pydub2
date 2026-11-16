import sys

import numpy as np
import onnx
from onnx import numpy_helper

# python tools/silero_weights.py silero_vad.onnx > src/silero_weights.h
# the stft basis is a plain hann windowed dft so vad.cpp builds it itself

names = [
    ("enc0_w", "encoder.0.reparam_conv.weight"),
    ("enc0_b", "encoder.0.reparam_conv.bias"),
    ("enc1_w", "encoder.1.reparam_conv.weight"),
    ("enc1_b", "encoder.1.reparam_conv.bias"),
    ("enc2_w", "encoder.2.reparam_conv.weight"),
    ("enc2_b", "encoder.2.reparam_conv.bias"),
    ("enc3_w", "encoder.3.reparam_conv.weight"),
    ("enc3_b", "encoder.3.reparam_conv.bias"),
    ("ih_w", "decoder.rnn.weight_ih"),
    ("hh_w", "decoder.rnn.weight_hh"),
    ("ih_b", "decoder.rnn.bias_ih"),
    ("hh_b", "decoder.rnn.bias_hh"),
    ("out_w", "decoder.decoder.2.weight"),
    ("out_b", "decoder.decoder.2.bias"),
]


def short(x):
    a = np.format_float_positional(x, unique=True, trim="-")
    b = np.format_float_scientific(x, unique=True, trim="-").replace("e+", "e").replace("e-0", "e-")
    s = min(a, b, key=len).replace("0.", ".", 1) if abs(x) < 1 else min(a, b, key=len)
    # the compiler reads it as a double and then rounds to float
    assert np.float32(float(s)) == x, s
    return s


model = onnx.load(sys.argv[1])
then = [a.g for a in model.graph.node[2].attribute if a.name == "then_branch"][0]
tensors = {}
for n in then.node:
    if n.op_type == "Constant":
        tensors[n.output[0].split("__")[-1]] = numpy_helper.to_array(n.attribute[0].t)

out = ["// silero vad v6.2, 16 khz weights. MIT, github.com/snakers4/silero-vad",
       "// made by tools/silero_weights.py, dont edit"]
for name, key in names:
    t = tensors[key].astype(np.float32).ravel()
    out.append("static const float %s[%d] = {" % (name, len(t)))
    line = ""
    for x in t:
        line += short(x) + "f,"
        if len(line) > 110:
            out.append(line)
            line = ""
    if line:
        out.append(line)
    out.append("};")
sys.stdout.write("\n".join(out) + "\n")
