"""Pure-numpy YAMNet: runs the int8 YAMNet TFLite model without a TFLite runtime.

HA's container is Alpine (musl) on Python 3.14, and no TFLite/LiteRT/ONNX wheel
exists for that, so the TFLite path in yamnet.py could never load there.
Weights + quantization params are extracted once from yamnet.tflite into
yamnet_weights.npz (tools/extract_yamnet_weights.py). Checked against the
official LiteRT runtime: same top class and cry score within one int8 step
on synthetic sounds and live crib audio; ~20-40 ms per 0.975 s frame on CPU.
"""
import numpy as np


class NumpyYamnet:
    """YAMNet (int8 TFLite) re-implemented in numpy: 15600 samples @16 kHz → 521 scores.

    Weights come from yamnet_weights.npz (extracted from yamnet.tflite). Activations
    are re-quantized to int8 after every layer exactly like the TFLite graph, so the
    output matches the official runtime to within rounding."""

    def __init__(self, npz_path: str) -> None:
        z = np.load(npz_path)
        self.hann = z["hann"].astype(np.float64)
        self.mel = z["mel"].astype(np.float64).T          # (257, 64)
        self.mel_add = z["mel_add"].astype(np.float64)
        self.in_s, self.in_z = float(z["in_s"][0]), int(z["in_z"][0])
        self.layers = []
        for n, (dw, stride) in enumerate(z["kinds"]):
            w = z[f"w{n}"].astype(np.float32) * z[f"ws{n}"].astype(np.float32).reshape(
                (1, 1, 1, -1) if dw else (-1, 1, 1, 1))
            xs = float(z[f"xs{n}"][0])
            b = z[f"b{n}"].astype(np.float32) * xs * z[f"ws{n}"].astype(np.float32)
            if dw:
                w = w[0]                                   # (3, 3, C)
            else:
                w = np.transpose(w, (1, 2, 3, 0))          # (kh, kw, Cin, Cout)
            self.layers.append((bool(dw), int(stride), w, b,
                                xs, int(z[f"xz{n}"][0]),
                                float(z[f"os{n}"][0]), int(z[f"oz{n}"][0])))
        self.fc_w = (z["fc_w"].astype(np.float32) * z["fc_ws"].astype(np.float32)[:, None]).T
        self.fc_xs = float(z["fc_xs"][0])
        self.fc_b = z["fc_b"].astype(np.float32) * self.fc_xs * z["fc_ws"].astype(np.float32)
        self.fc_os, self.fc_oz = float(z["fc_os"][0]), int(z["fc_oz"][0])
        self.mean_s, self.mean_z = float(z["mean_s"][0]), int(z["mean_z"][0])
        self.sig_s, self.sig_z = float(z["sig_s"][0]), int(z["sig_z"][0])

    @staticmethod
    def _q(x, s, z, lo=-128):
        return np.clip(np.round(x / s) + z, lo, 127)

    def features(self, wav: np.ndarray) -> np.ndarray:
        wav = np.asarray(wav, dtype=np.float64)
        idx = np.arange(400)[None, :] + 160 * np.arange(96)[:, None]
        spec = np.abs(np.fft.rfft(wav[idx] * self.hann, n=512, axis=1))
        return np.log(spec @ self.mel + self.mel_add)    # (96, 64)

    @staticmethod
    def _pad_same(x, stride):
        h, w = x.shape[:2]
        def p(n):
            out = -(-n // stride)
            tot = max((out - 1) * stride + 3 - n, 0)
            return tot // 2, tot - tot // 2
        (t, b), (l, r) = p(h), p(w)
        return np.pad(x, ((t, b), (l, r), (0, 0))), -(-h // stride), -(-w // stride)

    def __call__(self, wav: np.ndarray) -> np.ndarray:
        q = self._q(self.features(wav), self.in_s, self.in_z)
        x = ((q - self.in_z) * self.in_s).astype(np.float32)[:, :, None]  # (96, 64, 1)
        for dw, s, w, b, xs, xz, os_, oz in self.layers:
            if w.shape[0] == 3:
                xp, ho, wo = self._pad_same(x, s)
                acc = 0.0
                for dy in range(3):
                    for dx in range(3):
                        patch = xp[dy:dy + s * ho:s, dx:dx + s * wo:s]
                        acc = acc + (patch * w[dy, dx] if dw else patch @ w[dy, dx])
            else:                                           # 1x1 pointwise
                acc = x @ w[0, 0]
            acc = acc + b
            x = (self._q(acc, os_, oz, lo=oz) - oz) * os_   # ReLU via lower clamp at real 0
        m = (self._q(x.mean(axis=(0, 1)), self.mean_s, self.mean_z) - self.mean_z) * self.mean_s
        logits = (self._q(m @ self.fc_w + self.fc_b, self.fc_os, self.fc_oz) - self.fc_oz) * self.fc_os
        p = 1.0 / (1.0 + np.exp(-logits))
        return ((self._q(p, self.sig_s, self.sig_z) - self.sig_z) * self.sig_s).astype(np.float32)
