"""SIM — fake clock (T6): no wall-clock reads inside a simulated day.

The sim's determinism contract: the same seed produces the same leads, the
same decisions, byte-identical replays. Anything that calls time.time()
directly breaks that — the sim injects this clock everywhere the day-runner
reaches (T6b assembles the day; this is the seam it will use).
"""
import time
from typing import Optional


class FakeClock:
    """Deterministic time. Starts at an epoch anchor; advance() moves it.
    Real time is NEVER read."""

    def __init__(self, start_epoch: Optional[float] = None):
        # 2026-11-06 09:00 IST = 03:30 UTC — a business-morning anchor
        self._now = start_epoch if start_epoch is not None else 1793995800.0

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)

    def strftime(self, fmt: str) -> str:
        return time.strftime(fmt, time.gmtime(self._now))


def ist_hour(clock: FakeClock) -> int:
    """IST hour for the sim clock (the scheduler's business-hours input)."""
    return time.gmtime(clock.time() + 5.5 * 3600).tm_hour
