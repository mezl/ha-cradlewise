"""Pull YAMNet int8 weights + quant params out of yamnet.tflite into an npz."""
import sys, numpy as np
from ai_edge_litert.interpreter import Interpreter
src, dst = sys.argv[1], sys.argv[2]
it = Interpreter(model_path=src); it.allocate_tensors()
td = {t['index']: t for t in it.get_tensor_details()}
ops = [o for o in it._get_ops_details() if o['op_name'] != 'DELEGATE']
byname = {o['op_name']: o for o in ops}
def q(i):
    p = td[i]['quantization_parameters']; return p['scales'].astype(np.float32), p['zero_points'].astype(np.int32)
out = {}
# front end constants
out['hann'] = it.get_tensor(byname['MUL']['inputs'][1]).astype(np.float32)
fc_mel = [o for o in ops if o['op_name'] == 'FULLY_CONNECTED'][0]
out['mel'] = it.get_tensor(fc_mel['inputs'][1]).astype(np.float32)        # (64, 257)
out['mel_add'] = it.get_tensor(fc_mel['inputs'][2]).astype(np.float32)    # (64,)
s, z = q(byname['QUANTIZE']['outputs'][0]); out['in_s'], out['in_z'] = s, z
layers = [o for o in ops if o['op_name'] in ('CONV_2D', 'DEPTHWISE_CONV_2D')]
kinds = []
for n, o in enumerate(layers):
    x, w, b = o['inputs'][:3]
    ws, _ = q(w); os_, oz = q(o['outputs'][0]); xs, xz = q(x)
    out[f'w{n}'] = it.get_tensor(w).astype(np.int8)
    out[f'ws{n}'] = ws
    out[f'b{n}'] = it.get_tensor(b).astype(np.int32)
    out[f'xs{n}'], out[f'xz{n}'] = xs, xz
    out[f'os{n}'], out[f'oz{n}'] = os_, oz
    ish, osh = td[x]['shape'], td[o['outputs'][0]]['shape']
    stride = int(round(ish[1] / osh[1]))
    kinds.append((1 if o['op_name'] == 'DEPTHWISE_CONV_2D' else 0, stride))
out['kinds'] = np.array(kinds, dtype=np.int32)
fc = [o for o in ops if o['op_name'] == 'FULLY_CONNECTED'][1]
x, w, b = fc['inputs'][:3]
out['fc_w'] = it.get_tensor(w).astype(np.int8); out['fc_ws'], _ = q(w)
out['fc_b'] = it.get_tensor(b).astype(np.int32)
out['fc_xs'], out['fc_xz'] = q(x); out['fc_os'], out['fc_oz'] = q(fc['outputs'][0])
mean = byname['MEAN']; out['mean_s'], out['mean_z'] = q(mean['outputs'][0])
lg = byname['LOGISTIC']; out['sig_s'], out['sig_z'] = q(lg['outputs'][0])
# frame indices used by the graph's GATHER (sanity)
print({k: v.shape for k, v in out.items() if k in ('hann','mel','mel_add','w0','w1','w26','fc_w')}, out['kinds'].tolist())
print('mean q', out['mean_s'], out['mean_z'], 'fc out', out['fc_os'], out['fc_oz'], 'sig', out['sig_s'], out['sig_z'])
np.savez_compressed(dst, **out)
