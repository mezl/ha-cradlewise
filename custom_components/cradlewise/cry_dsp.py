"""Pure-DSP infant-cry detection — no Home Assistant / aiortc dependencies.

Isolated here (rather than inline in camera.py) so the signal-processing logic
can be unit-tested with only numpy installed.  camera.py imports CryDetector
and feeds it mono float32 audio; everything else stays the same.

Detection model
---------------
Infant crying sits in the 300–2500 Hz band with a *structured* (tonal) spectrum
that rides well above the noise floor.  Three independent gates reject the common
false positives:

  * RMS gate            → silence
  * Spectral flatness   → broadband noise (white-noise machine, fan hiss)
  * Spectral centroid   → low-frequency hum (HVAC, traffic rumble)
  * Cry-band energy     → energy must actually be concentrated in the cry band

A rolling majority vote over consecutive 1-second windows debounces the final
binary state so a single noisy window cannot flip the sensor.
"""
from __future__ import annotations

import collections

import numpy as np

# ── Tunable constants (kept identical to the original camera.py values) ──────────

CRY_WINDOW_SAMPLES = 48000   # 1 s at 48 kHz
CRY_VOTE_LEN = 5             # rolling vote window length
CRY_VOTE_THRESHOLD = 3       # positive windows needed to confirm crying

# Spectral Flatness Measure: below this → tonal/voiced (not broadband noise)
SFM_MAX = 0.30

# Spectral centroid range for infant crying (Hz)
CENTROID_LO = 400
CENTROID_HI = 2200

# Fraction of total spectral energy that must fall in the cry band (300–2500 Hz)
CRY_BAND_RATIO_MIN = 0.40

# Minimum RMS to bother analysing (below ≈ −28 dBFS → treat as silence)
RMS_MIN = 0.04

_REF_RATE = 48000


def analyse_cry_window(audio: np.ndarray, sample_rate: int = _REF_RATE) -> bool:
    """Return True if a 1-second mono window has infant-cry characteristics.

    `audio` is a float32 array in [-1, 1].  `sample_rate` is the rate of that
    array (after any decimation the caller already applied).
    """
    audio = np.asarray(audio, dtype=np.float32)
    n = len(audio)
    if n == 0:
        return False

    rms = float(np.sqrt(np.mean(audio ** 2)))
    if rms < RMS_MIN:
        return False

    windowed = audio * np.hanning(n)
    fft_mag = np.abs(np.fft.rfft(windowed))
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)

    # ── Spectral Flatness Measure (200–6000 Hz) ─────────────────────────────
    sfm_mask = (freqs >= 200) & (freqs <= 6000)
    if not np.any(sfm_mask):
        return False
    sub = fft_mag[sfm_mask] + 1e-10
    sfm = float(np.exp(np.mean(np.log(sub))) / np.mean(sub))
    if sfm > SFM_MAX:
        return False  # too noise-like (white / pink noise)

    # ── Spectral centroid (200–6000 Hz) ─────────────────────────────────────
    centroid = float(
        np.sum(freqs[sfm_mask] * fft_mag[sfm_mask])
        / (np.sum(fft_mag[sfm_mask]) + 1e-10)
    )
    if not (CENTROID_LO <= centroid <= CENTROID_HI):
        return False  # too low (hum) or too high (hiss)

    # ── Cry-band energy ratio (300–2500 Hz) ─────────────────────────────────
    cry_mask = (freqs >= 300) & (freqs <= 2500)
    total_energy = float(np.sum(fft_mag ** 2)) + 1e-10
    cry_ratio = float(np.sum(fft_mag[cry_mask] ** 2)) / total_energy
    if cry_ratio < CRY_BAND_RATIO_MIN:
        return False

    return True


class CryDetector:
    """Stateful detector: accumulate mono audio, analyse per 1-second window, vote.

    Feed mono float32 samples via :meth:`feed`.  When the debounced crying state
    *changes*, ``feed`` returns the new boolean; otherwise it returns ``None``.
    This keeps all I/O (HA bus events, listener callbacks) out of the DSP layer.
    """

    def __init__(self) -> None:
        self._buf: list[float] = []
        self._votes: collections.deque[bool] = collections.deque(maxlen=CRY_VOTE_LEN)
        self.is_crying = False

    def feed(self, mono: np.ndarray, sample_rate: int = _REF_RATE) -> bool | None:
        mono = np.asarray(mono, dtype=np.float32)

        # Decimate non-48 kHz audio down to the 48 kHz reference.
        if sample_rate != _REF_RATE:
            ratio = sample_rate // _REF_RATE
            if ratio > 1:
                mono = mono[::ratio]

        self._buf.extend(mono.tolist())
        if len(self._buf) < CRY_WINDOW_SAMPLES:
            return None

        window = np.asarray(self._buf[:CRY_WINDOW_SAMPLES], dtype=np.float32)
        self._buf = self._buf[CRY_WINDOW_SAMPLES:]

        self._votes.append(analyse_cry_window(window, _REF_RATE))
        new_state = sum(self._votes) >= CRY_VOTE_THRESHOLD
        if new_state != self.is_crying:
            self.is_crying = new_state
            return new_state
        return None
