#!/usr/bin/env python3
"""
OSC Activity Model

Qt-free logic behind the Live pane's OSC activity column (#66): which address
rows to show for a protocol and tracking mode, and per-second rates computed
from the status channel's cumulative `per_address` counts.

`predict_families()` takes its rows from the shared address map in
`src/osc_preview.py`, which runs the real `OscEmitter` on a synthetic frame, so
it cannot drift from the wire format.
"""

# ============================================================================
# IMPORTS
# ============================================================================
from __future__ import annotations

import re
from collections import deque
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional

from src.osc_protocol import DEFAULT_OSC_PROTOCOL

# ============================================================================
# CONSTANTS
# ============================================================================
RATE_WINDOW = 1.0          # Seconds of status history a rate is averaged over
_LM_TAIL = re.compile(r'/lm/\d+$')

POSE_LANDMARKS = 33
HAND_LANDMARKS = 21


# ============================================================================
# ADDRESS FAMILIES
# ============================================================================
def family_of(address: str) -> str:
    """
    Collapse a per-landmark address to its family.

    Args:
        address: A sent OSC address, e.g. '/left_hand/world/lm/7'.

    Returns:
        '/left_hand/world/lm/*' for per-landmark addresses, else the address.
    """
    return _LM_TAIL.sub('/lm/*', address)


class _Capture:
    """Sender stand-in: records nothing, answers the stats the emitter reads."""

    def send_packet(self, _packet) -> None:
        return None

    def get_stats(self) -> dict:
        return {'sent': 0, 'dropped': 0, 'queued': 0}


def _landmarks(count: int, world: bool = False) -> list:
    return [SimpleNamespace(x=0.5, y=0.5, z=0.0, visibility=1.0, presence=1.0) for _ in range(count)]


def predict_families(protocol: str, mode: str) -> List[str]:
    """
    The address rows a receiver sees for a protocol and tracking mode.

    Delegates to the shared address map (`src/osc_preview.py`), which runs the
    real emitter on a synthetic frame, so status, tracking and heartbeat
    channels appear alongside the data addresses.

    Args:
        protocol: osc.protocol value (legacy, json or float).
        mode: ui.mode value (pose, hand or all).

    Returns:
        Family strings in send order (per-landmark addresses collapsed to `.../lm/*`).
    """
    from src.osc_preview import addresses
    return [address for address, _payload, _rate in addresses(protocol or DEFAULT_OSC_PROTOCOL, mode)]


# ============================================================================
# RATE TRACKER
# ============================================================================
class ActivityTracker:
    """
    Per-family message rates from cumulative per-address counts.

    Feed it every status message. Rates average over the last RATE_WINDOW
    seconds of status `uptime`, so they do not jitter with the 10 Hz status
    cadence. A restart (uptime or counts going backwards) drops the history.
    """

    def __init__(self, families: Iterable[str] = ()):
        self.families: List[str] = list(families)
        self._samples: deque = deque()   # (t, {family: cumulative count})
        self._family_cache: Dict[str, str] = {}

    def set_families(self, families: Iterable[str]) -> None:
        """Replace the row list (protocol or mode changed). History is kept."""
        self.families = list(families)

    def reset(self) -> None:
        """Forget all history (engine stopped or restarted)."""
        self._samples.clear()

    def feed(self, status: dict) -> List[str]:
        """
        Add one status message.

        Args:
            status: A status-channel dict (uses `uptime` and `per_address`).

        Returns:
            Families seen for the first time (appended to `families`).
        """
        per_address = status.get('per_address') or {}
        counts: Dict[str, int] = {}
        cache = self._family_cache
        for address, n in per_address.items():
            fam = cache.get(address)
            if fam is None:
                fam = cache[address] = family_of(address)
            counts[fam] = counts.get(fam, 0) + int(n)
        t = float(status.get('uptime') or 0.0)

        if self._samples:
            last_t, last_counts = self._samples[-1]
            if t < last_t or sum(counts.values()) < sum(last_counts.values()):
                self._samples.clear()
        self._samples.append((t, counts))
        # Keep one sample at or before the window start, drop anything older
        while len(self._samples) > 2 and self._samples[1][0] <= t - RATE_WINDOW:
            self._samples.popleft()

        new = [f for f in counts if f not in self.families]
        self.families.extend(new)
        return new

    def rate(self, family: str) -> float:
        """Messages per second for a family over the window (0 until two samples)."""
        if len(self._samples) < 2:
            return 0.0
        (t0, c0), (t1, c1) = self._samples[0], self._samples[-1]
        if t1 <= t0:
            return 0.0
        return max(0.0, (c1.get(family, 0) - c0.get(family, 0)) / (t1 - t0))

    def total_rate(self) -> float:
        """Messages per second across every address."""
        if len(self._samples) < 2:
            return 0.0
        (t0, c0), (t1, c1) = self._samples[0], self._samples[-1]
        if t1 <= t0:
            return 0.0
        return max(0.0, (sum(c1.values()) - sum(c0.values())) / (t1 - t0))

    def rates(self) -> Dict[str, float]:
        """Rate for every row family."""
        return {f: self.rate(f) for f in self.families}


# ============================================================================
# FORMATTING
# ============================================================================
def format_uptime(seconds: Optional[float]) -> str:
    """hh:mm:ss for an uptime in seconds (00:00:00 when unknown)."""
    s = max(0, int(seconds or 0))
    return '{:02d}:{:02d}:{:02d}'.format(s // 3600, (s % 3600) // 60, s % 60)


def landmark_points(pose: int, hands: int) -> int:
    """Landmarks tracked (and sent) per frame for a detection count."""
    return int(pose) * POSE_LANDMARKS + int(hands) * HAND_LANDMARKS
