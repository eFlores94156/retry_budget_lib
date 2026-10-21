"""Core retry-budget implementation.

A retry budget is a cap on how many retries a caller may issue for a given
dependency within a sliding time window. The purpose is to stop a failing
dependency from amplifying load: when a downstream service is degraded,
unbounded retries turn every request into N requests, which can take that
dependency down entirely. A shared budget forces all callers to compete for
the same retry allowance, so a single caller cannot silently exhaust capacity.

Design decisions
---------------
* **Sliding window via timestamp deque.** We keep an explicit deque of
  retry timestamps and drop entries that have aged out on every call. This
  is O(n) in the number of retries in the window, but for a retry budget the
  window is small and retry counts are bounded, so the deque stays short.
  The alternative — a token-bucket with a continuous refill rate — is cheaper
  but makes the current allowance depend on elapsed time, which makes it
  harder to reason about in tests and harder to reason about under load
  spikes. Explicit timestamps keep behaviour transparent.
* **Rejected retries do not consume budget.** A `record_retry` call only
  pushes a timestamp when the caller actually issues a retry. If the budget
  says `allow_retry()` is False, the caller must not retry, and therefore
  must not `record_retry`. This is a deliberate choice: it means the budget
  only ever counts real work, not rejected attempts.
* **`clock` is injectable.** All time-dependent behaviour is driven by the
  `clock` callable so tests are fully deterministic.
* **Primary retries do not consume the budget.** Only *retries* do. A
  normal request is not a retry; a retry is a second-or-later attempt. The
  caller decides what counts as a retry.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque


__all__ = ["RetryBudget", "BudgetExhausted"]


class BudgetExhausted(Exception):
    """Raised when a retry is requested but the budget is spent.

    This is an exception rather than a boolean return on `attempt_retry` so
    that the budget check and the bookkeeping happen atomically: there is no
    way for a caller to forget to record a retry after `allow_retry()` returns
    True, because `attempt_retry` does both in one call.
    """


class RetryBudget:
    """A sliding-window retry budget shared across callers.

    Parameters
    ----------
    max_retries:
        The maximum number of retries permitted within the rolling window.
        Must be a non-negative integer.
    window_seconds:
        The length of the rolling window in seconds. Must be positive.
    clock:
        A callable returning the current time as a float, in seconds.
        Defaults to ``time.monotonic``. Injected so tests can control time.

    Notes
    -----
    The budget counts *retry attempts*, not distinct requests. The window is
    sliding: an old retry falls out of the count exactly ``window_seconds``
    after it was recorded.
    """

    def __init__(
        self,
        max_retries: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(max_retries, int) or isinstance(max_retries, bool):
            raise TypeError("max_retries must be an int")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if not callable(clock):
            raise TypeError("clock must be callable")

        self._max_retries = max_retries
        self._window = float(window_seconds)
        self._clock = clock
        self._timestamps: Deque[float] = deque()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _prune(self) -> None:
        """Drop timestamps older than the window boundary.

        Called on every read and write so the deque always reflects only
        retries still inside the window.
        """
        cutoff = self._clock() - self._window
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def current_count(self) -> int:
        """Return the number of retries recorded in the current window."""
        self._prune()
        return len(self._timestamps)

    def allow_retry(self) -> bool:
        """Return True if a retry would fit within the budget, else False."""
        return self.current_count() < self._max_retries

    def record_retry(self) -> None:
        """Record that a retry was issued.

        Raises ``BudgetExhausted`` if recording this retry would push the
        count over the limit. A caller should call this *after* issuing the
        retry (or immediately before, atomically) so the budget reflects real
        work.
        """
        self._prune()
        if len(self._timestamps) >= self._max_retries:
            raise BudgetExhausted(
                "retry budget exhausted: %d retries already recorded in "
                "window" % self._max_retries
            )
        self._timestamps.append(self._clock())

    def attempt_retry(self) -> None:
        """Atomically check and consume one unit of retry budget.

        Raises ``BudgetExhausted`` if the budget cannot accommodate another
        retry. Using this instead of the separate ``allow_retry`` /
        ``record_retry`` pair closes the gap where a caller forgets to record,
        or records without checking.
        """
        self._prune()
        if len(self._timestamps) >= self._max_retries:
            raise BudgetExhausted(
                "retry budget exhausted: %d retries already recorded in "
                "window" % self._max_retries
            )
        self._timestamps.append(self._clock())

    def reset(self) -> None:
        """Clear all recorded retries.

        Intended for tests or for administrative resets after a known
        dependency recovery. Normal callers should not call this; the window
        rolling forward is the expected way retries expire.
        """
        self._timestamps.clear()
