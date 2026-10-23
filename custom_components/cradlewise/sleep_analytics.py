"""Local sleep analytics — derived from MQTT shadow state transitions."""
from __future__ import annotations

import json
import logging
import threading
from datetime import date, datetime, timezone
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

_SLEEP_STATE_SLEEPING = 4
_MIN_NAP_MINUTES = 1.0  # ignore sub-minute state glitches


class SleepTracker:
    """Tracks nap sessions and soothing events from shadow state changes.

    All public properties are safe to call from any thread.
    Persists daily data to a JSON file in HA's .storage directory so naps
    survive HA restarts.
    """

    def __init__(self, storage_path: Path) -> None:
        self._path = storage_path
        self._lock = threading.Lock()

        self._date: str = ""
        self._naps: list[dict] = []           # {start, end, minutes}
        self._current_nap_start: str | None = None
        self._soothing_count: int = 0
        self._was_soothing: bool = False
        self._soothing_events: list[str] = []  # ISO timestamps of soothe starts
        self._awake_periods: list[dict] = []   # {start, end, minutes}
        self._current_awake_start: str | None = None

        self._load()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._path.exists():
                data = json.loads(self._path.read_text())
                if data.get("date") == date.today().isoformat():
                    self._date = data["date"]
                    self._naps = data.get("naps", [])
                    self._current_nap_start = data.get("current_nap_start")
                    self._soothing_count = data.get("soothing_count", 0)
                    self._soothing_events = data.get("soothing_events", [])
                    self._awake_periods = data.get("awake_periods", [])
                    self._current_awake_start = data.get("current_awake_start")
                    return
        except Exception as exc:
            _LOGGER.warning("Could not load sleep data: %s", exc)
        self._reset_day()

    def _persist(self) -> None:
        try:
            self._path.write_text(json.dumps({
                "date": self._date,
                "naps": self._naps,
                "current_nap_start": self._current_nap_start,
                "soothing_count": self._soothing_count,
                "soothing_events": self._soothing_events,
                "awake_periods": self._awake_periods,
                "current_awake_start": self._current_awake_start,
            }))
        except Exception as exc:
            _LOGGER.warning("Could not persist sleep data: %s", exc)

    def _reset_day(self) -> None:
        self._date = date.today().isoformat()
        self._naps = []
        self._current_nap_start = None
        self._soothing_count = 0
        self._was_soothing = False
        self._soothing_events = []
        self._awake_periods = []
        self._current_awake_start = None

    # ── State ingestion ───────────────────────────────────────────────────

    def on_state_update(self, state: dict) -> None:
        """Called by coordinator on every MQTT update (paho thread)."""
        with self._lock:
            if self._date != date.today().isoformat():
                self._reset_day()

            now_iso = datetime.now(timezone.utc).isoformat()
            sleep_state = state.get("babySleepState")
            is_sleeping = (sleep_state == _SLEEP_STATE_SLEEPING)

            if is_sleeping and self._current_nap_start is None:
                self._current_nap_start = now_iso

            elif not is_sleeping and self._current_nap_start is not None:
                start_dt = datetime.fromisoformat(self._current_nap_start)
                end_dt = datetime.fromisoformat(now_iso)
                minutes = (end_dt - start_dt).total_seconds() / 60
                if minutes >= _MIN_NAP_MINUTES:
                    self._naps.append({
                        "start": self._current_nap_start,
                        "end": now_iso,
                        "minutes": round(minutes, 1),
                    })
                self._current_nap_start = None

            is_soothing = bool(state.get("isCribHelping"))
            if is_soothing and not self._was_soothing:
                self._soothing_count += 1
                self._soothing_events.append(now_iso)
            self._was_soothing = is_soothing

            # Awake time = in the crib but not asleep. Tracked as intervals
            # between transitions (exactly like naps) rather than by summing
            # the gaps between shadow updates — the crib only publishes on
            # change, so a quiet 40-minute awake stretch may be a single
            # message and gap-summing would lose most of it.
            is_awake = bool(state.get("babyPresent")) and not is_sleeping
            if is_awake and self._current_awake_start is None:
                self._current_awake_start = now_iso
            elif not is_awake and self._current_awake_start is not None:
                start_dt = datetime.fromisoformat(self._current_awake_start)
                minutes = (
                    datetime.fromisoformat(now_iso) - start_dt
                ).total_seconds() / 60
                if minutes >= _MIN_NAP_MINUTES:
                    self._awake_periods.append({
                        "start": self._current_awake_start,
                        "end": now_iso,
                        "minutes": round(minutes, 1),
                    })
                self._current_awake_start = None

            self._persist()

    # ── Analytics ─────────────────────────────────────────────────────────

    @property
    def nap_count(self) -> int:
        with self._lock:
            return len(self._naps)

    @property
    def total_sleep_minutes(self) -> float:
        with self._lock:
            total = sum(n["minutes"] for n in self._naps)
            if self._current_nap_start is not None:
                start_dt = datetime.fromisoformat(self._current_nap_start)
                total += (datetime.now(timezone.utc) - start_dt).total_seconds() / 60
            return round(total, 1)

    @property
    def longest_nap_minutes(self) -> float | None:
        with self._lock:
            return max((n["minutes"] for n in self._naps), default=None)

    @property
    def last_nap_start(self) -> datetime | None:
        with self._lock:
            # Return ongoing nap start if no completed naps yet
            src = self._naps[-1]["start"] if self._naps else self._current_nap_start
            return datetime.fromisoformat(src) if src else None

    @property
    def last_nap_end(self) -> datetime | None:
        with self._lock:
            if not self._naps:
                return None
            return datetime.fromisoformat(self._naps[-1]["end"])

    @property
    def soothing_count(self) -> int:
        with self._lock:
            return self._soothing_count

    @property
    def total_awake_minutes(self) -> float:
        with self._lock:
            total = sum(a["minutes"] for a in self._awake_periods)
            if self._current_awake_start is not None:
                start_dt = datetime.fromisoformat(self._current_awake_start)
                total += (
                    datetime.now(timezone.utc) - start_dt
                ).total_seconds() / 60
            return round(total, 1)

    @property
    def soothing_events(self) -> list[str]:
        """ISO timestamps of today's soothe starts (oldest first)."""
        with self._lock:
            return list(self._soothing_events)

    @property
    def awake_sessions(self) -> list[dict]:
        """Today's awake-in-crib intervals, including one in progress.

        Published alongside the naps so the card can draw awake time it has
        actually observed, instead of assuming every gap between naps was
        awake-in-crib (it may have been out of the crib entirely).
        """
        with self._lock:
            out = [dict(a) for a in self._awake_periods]
            if self._current_awake_start is not None:
                start_dt = datetime.fromisoformat(self._current_awake_start)
                minutes = (
                    datetime.now(timezone.utc) - start_dt
                ).total_seconds() / 60
                out.append({
                    "start": self._current_awake_start,
                    "end": None,
                    "minutes": round(minutes, 1),
                })
            return out

    @property
    def sessions(self) -> list[dict]:
        """Today's sleep sessions, including one still in progress.

        An ongoing nap is returned with ``end: None``. This is the raw feed the
        Lovelace card draws its day timeline from, so it works with no recorder
        history available.
        """
        with self._lock:
            out = [dict(n) for n in self._naps]
            if self._current_nap_start is not None:
                start_dt = datetime.fromisoformat(self._current_nap_start)
                minutes = (
                    datetime.now(timezone.utc) - start_dt
                ).total_seconds() / 60
                out.append({
                    "start": self._current_nap_start,
                    "end": None,
                    "minutes": round(minutes, 1),
                })
            return out
