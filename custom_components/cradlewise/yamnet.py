"""YAMNet infant-cry detection — a learned alternative to the hand-tuned DSP gates.

This ports the *value* of the `crywatch` project (Google's AudioSet-trained YAMNet
classifier) into the component, fed by the **same** crib audio the DSP `CryDetector`
already receives. It runs **alongside** `cry_dsp.py` so the two can be compared /
ensembled — the DSP sensor is untouched.

Design mirrors `cry_dsp.py`: the pure signal + state-machine logic (`CryClassifier`)
has no TensorFlow / Home Assistant dependency and is unit-tested with a fake scorer.
`YamnetScorer` is the thin TFLite wrapper (`yamnet_numpy.NumpyYamnet` is the
dependency-free fallback); `build_default()` wires them together and returns
``None`` if the model files are missing — so the component still
loads (YAMNet entities simply report unavailable) and the DSP detector is unaffected.

Model files (not committed; see docs/CRYWATCH_YAMNET.md to fetch, ~4 MB):
    <component>/yamnet.tflite
    <component>/yamnet_class_map.csv   (521 rows: index,mid,display_name)

YAMNet: 16 kHz mono waveform in → 521 AudioSet class scores out, per 0.975 s frame.
"""
from __future__ import annotations

import csv
import os
from typing import Callable, Optional, Sequence

import numpy as np

# ── Tunables (defaults follow crywatch) ─────────────────────────────────────────

# AudioSet display names aggregated into the "crying" probability (max across them).
TARGET_CLASS_NAMES = ("Baby cry, infant cry", "Crying, sobbing")

FRAME_SAMPLES = 15600     # YAMNet's native window: 0.975 s @ 16 kHz
HOP_SAMPLES = 7680        # ~0.48 s hop → ~2 evaluations / second
TARGET_RATE = 16000       # YAMNet input rate

CRY_THRESHOLD = 0.4       # per-frame probability above which a frame is "positive"
SUSTAIN_ON = 2            # consecutive positive frames needed to declare crying
SUSTAIN_OFF = 4           # consecutive non-positive frames needed to clear (~2 s quiet)


def _resample_to_16k(mono: np.ndarray, sample_rate: int) -> np.ndarray:
    """Linear-resample a mono float32 array to 16 kHz. General (any input rate)."""
    mono = np.asarray(mono, dtype=np.float32)
    if sample_rate == TARGET_RATE or mono.size == 0:
        return mono
    n_out = int(round(mono.size * TARGET_RATE / sample_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    x_old = np.linspace(0.0, 1.0, mono.size, endpoint=False)
    x_new = np.linspace(0.0, 1.0, n_out, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32)


def load_class_indices(csv_path: str, names: Sequence[str] = TARGET_CLASS_NAMES) -> list[int]:
    """Return the row indices in yamnet_class_map.csv whose display_name matches `names`."""
    wanted = {n.strip().lower() for n in names}
    idx: list[int] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader, None)  # index,mid,display_name
        for row in reader:
            if len(row) < 3:
                continue
            if row[2].strip().lower() in wanted:
                idx.append(int(row[0]))
    return idx


class CryClassifier:
    """Pure, testable: accumulate mono audio → per-frame YAMNet scoring → debounced state.

    `scorer` maps a 16 kHz float32 frame to a 1-D score vector; `class_indices` selects
    the crying-related classes (probability = max across them). Inject a fake `scorer`
    in tests — no TensorFlow needed. :meth:`feed` returns ``(changed, prob)`` where
    ``changed`` is the new boolean state on a transition else ``None``, and ``prob`` is
    the most recent frame probability (0 until the first full frame).
    """

    def __init__(
        self,
        scorer: Callable[[np.ndarray], np.ndarray],
        class_indices: Sequence[int],
        *,
        threshold: float = CRY_THRESHOLD,
        sustain_on: int = SUSTAIN_ON,
        sustain_off: int = SUSTAIN_OFF,
        frame_samples: int = FRAME_SAMPLES,
        hop_samples: int = HOP_SAMPLES,
        src_rate: int = 48000,
    ) -> None:
        self._scorer = scorer
        self._class_indices = list(class_indices)
        self._threshold = threshold
        self._sustain_on = sustain_on
        self._sustain_off = sustain_off
        self._frame = frame_samples
        self._hop = hop_samples
        self._src_rate = src_rate

        self._buf = np.zeros(0, dtype=np.float32)
        self._since_last = hop_samples   # so the first full frame evaluates immediately
        self._pos_run = 0
        self._neg_run = 0
        self.is_crying = False
        self.prob = 0.0

    def _prob_from_scores(self, scores: np.ndarray) -> float:
        scores = np.asarray(scores, dtype=np.float32).ravel()
        if not self._class_indices:
            return 0.0
        vals = [scores[i] for i in self._class_indices if 0 <= i < scores.size]
        return float(max(vals)) if vals else 0.0

    def feed(self, mono: np.ndarray, sample_rate: int) -> tuple[Optional[bool], float]:
        resampled = _resample_to_16k(mono, sample_rate)
        if resampled.size:
            self._buf = np.concatenate([self._buf, resampled])
            self._since_last += resampled.size

        changed: Optional[bool] = None
        while self._buf.size >= self._frame and self._since_last >= self._hop:
            frame = self._buf[-self._frame:]
            self.prob = self._prob_from_scores(self._scorer(frame))
            self._since_last = 0
            # keep only the most recent frame's worth of audio (bounded memory)
            self._buf = self._buf[-self._frame:]

            if self.prob >= self._threshold:
                self._pos_run += 1
                self._neg_run = 0
                if not self.is_crying and self._pos_run >= self._sustain_on:
                    self.is_crying = True
                    changed = True
            else:
                self._neg_run += 1
                self._pos_run = 0
                if self.is_crying and self._neg_run >= self._sustain_off:
                    self.is_crying = False
                    changed = False

        return changed, self.prob


class YamnetScorer:
    """TFLite YAMNet wrapper: 16 kHz float32 waveform → 521-class score vector."""

    def __init__(self, model_path: str) -> None:
        Interpreter = _load_interpreter_cls()
        self._interp = Interpreter(model_path=model_path)
        self._interp.allocate_tensors()
        self._in = self._interp.get_input_details()[0]
        self._out = self._interp.get_output_details()

    def __call__(self, waveform: np.ndarray) -> np.ndarray:
        wf = np.asarray(waveform, dtype=np.float32).ravel()
        idx = self._in["index"]
        # YAMNet's classification TFLite takes a 1-D [num_samples] input; resize to fit.
        try:
            self._interp.resize_tensor_input(idx, [int(wf.size)], strict=False)
            self._interp.allocate_tensors()
            self._interp.set_tensor(idx, wf)
        except Exception:
            # Fallback for fixed [1, N] input variants.
            self._interp.set_tensor(idx, wf.reshape(self._in["shape"]))
        self._interp.invoke()
        scores = None
        for od in self._out:
            t = self._interp.get_tensor(od["index"])
            if t.shape and t.shape[-1] == 521:
                scores = t
                break
        if scores is None:
            scores = self._interp.get_tensor(self._out[0]["index"])
        scores = np.asarray(scores, dtype=np.float32)
        if scores.ndim > 1:                       # [frames, 521] → mean over frames
            scores = scores.reshape(-1, scores.shape[-1]).mean(axis=0)
        return scores


def _load_interpreter_cls():
    """Return a TFLite Interpreter class from whichever runtime is installed."""
    try:
        from ai_edge_litert.interpreter import Interpreter  # LiteRT (current)
        return Interpreter
    except ImportError:
        pass
    try:
        from tflite_runtime.interpreter import Interpreter   # legacy tflite-runtime
        return Interpreter
    except ImportError:
        pass
    from tensorflow.lite import Interpreter                  # full TensorFlow fallback
    return Interpreter


def build_default(component_dir: str) -> Optional[CryClassifier]:
    """Wire a real YAMNet-backed CryClassifier, or ``None`` if model files are missing.

    Uses a TFLite runtime when one is installed, else the pure-numpy port
    (yamnet_numpy.py) — HA's musl/Python 3.14 container has no TFLite wheel."""
    model = os.path.join(component_dir, "yamnet.tflite")
    weights = os.path.join(component_dir, "yamnet_weights.npz")
    class_map = os.path.join(component_dir, "yamnet_class_map.csv")
    if not os.path.exists(class_map):
        return None
    indices = load_class_indices(class_map)
    if not indices:
        return None
    scorer = None
    if os.path.exists(model):
        try:
            scorer = YamnetScorer(model)
            backend = "tflite"
        except Exception:
            scorer = None
    if scorer is None and os.path.exists(weights):
        try:
            from .yamnet_numpy import NumpyYamnet
        except ImportError:  # loaded outside the package (tests)
            from yamnet_numpy import NumpyYamnet
        try:
            scorer = NumpyYamnet(weights)
            backend = "numpy"
        except Exception:
            scorer = None
    if scorer is None:
        return None
    clf = CryClassifier(scorer, indices)
    clf.backend = backend
    return clf
