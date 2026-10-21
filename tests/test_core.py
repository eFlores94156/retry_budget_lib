"""Tests for retry_budget_lib.core.RetryBudget."""

import unittest

from retry_budget_lib.core import RetryBudget, BudgetExhausted


class FakeClock:
    """Deterministic clock for tests.

    Exposes the current time and lets tests advance it by an explicit delta,
    so no test ever sleeps or reads wall-clock time.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += float(delta)


class TestRetryBudgetConstruction(unittest.TestCase):
    def test_max_retries_must_be_int(self):
        with self.assertRaises(TypeError):
            RetryBudget(max_retries=1.5, window_seconds=10, clock=FakeClock())  # type: ignore[arg-type]

    def test_max_retries_bool_rejected(self):
        # bool is a subclass of int but is not a meaningful retry count.
        with self.assertRaises(TypeError):
            RetryBudget(max_retries=True, window_seconds=10, clock=FakeClock())  # type: ignore[arg-type]

    def test_max_retries_negative_rejected(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_retries=-1, window_seconds=10, clock=FakeClock())

    def test_window_seconds_zero_rejected(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_retries=1, window_seconds=0, clock=FakeClock())

    def test_window_seconds_negative_rejected(self):
        with self.assertRaises(ValueError):
            RetryBudget(max_retries=1, window_seconds=-1, clock=FakeClock())

    def test_clock_must_be_callable(self):
        with self.assertRaises(TypeError):
            RetryBudget(max_retries=1, window_seconds=10, clock="not callable")  # type: ignore[arg-type]

    def test_zero_budget_allows_no_retries(self):
        # max_retries=0 is a valid configuration: a budget that bans retries.
        b = RetryBudget(max_retries=0, window_seconds=10, clock=FakeClock())
        self.assertFalse(b.allow_retry())
        with self.assertRaises(BudgetExhausted):
            b.record_retry()
        with self.assertRaises(BudgetExhausted):
            b.attempt_retry()
        self.assertEqual(b.current_count(), 0)


class TestRetryBudgetSlidingWindow(unittest.TestCase):
    def test_budget_allows_up_to_max_then_rejects(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=3, window_seconds=10, clock=clk)
        self.assertTrue(b.allow_retry())
        b.record_retry()
        self.assertEqual(b.current_count(), 1)
        self.assertTrue(b.allow_retry())
        b.record_retry()
        self.assertEqual(b.current_count(), 2)
        self.assertTrue(b.allow_retry())
        b.record_retry()
        self.assertEqual(b.current_count(), 3)
        self.assertFalse(b.allow_retry())
        with self.assertRaises(BudgetExhausted):
            b.record_retry()
        self.assertEqual(b.current_count(), 3)

    def test_old_retries_expire_from_window(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=2, window_seconds=10, clock=clk)

        b.record_retry()  # t=0
        b.record_retry()  # t=0
        self.assertEqual(b.current_count(), 2)
        self.assertFalse(b.allow_retry())

        clk.advance(11)  # both retries now older than the 10s window
        self.assertEqual(b.current_count(), 0)
        self.assertTrue(b.allow_retry())

    def test_boundary_expiry_is_strictly_greater(self):
        # A retry recorded at t=0 should still count at t=10 (window is
        # [0, 10] inclusive of the left edge, exclusive of the right edge?
        # We chose: an entry expires when now - entry >= window. So at t=10,
        # an entry at t=0 has expired. This matches the <= cutoff used in
        # _prune and is the edge we test here.)
        clk = FakeClock()
        b = RetryBudget(max_retries=1, window_seconds=10, clock=clk)
        b.record_retry()  # t=0
        clk.advance(9)
        self.assertEqual(b.current_count(), 1)
        clk.advance(1)  # now t=10, entry at t=0 is exactly window-seconds old
        self.assertEqual(b.current_count(), 0)

    def test_partial_expiry(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=2, window_seconds=10, clock=clk)

        b.record_retry()  # t=0
        clk.advance(5)
        b.record_retry()  # t=5
        self.assertEqual(b.current_count(), 2)

        clk.advance(5)  # now t=10; first retry (t=0) expires, second (t=5) stays
        self.assertEqual(b.current_count(), 1)
        self.assertTrue(b.allow_retry())

    def test_attempt_retry_is_atomic_check_and_record(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=1, window_seconds=10, clock=clk)
        b.attempt_retry()  # consumes the one slot
        self.assertEqual(b.current_count(), 1)
        with self.assertRaises(BudgetExhausted):
            b.attempt_retry()
        # state unchanged after the failed attempt
        self.assertEqual(b.current_count(), 1)

    def test_reset_clears_budget(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=2, window_seconds=10, clock=clk)
        b.record_retry()
        b.record_retry()
        self.assertEqual(b.current_count(), 2)
        b.reset()
        self.assertEqual(b.current_count(), 0)
        self.assertTrue(b.allow_retry())

    def test_rejected_retry_does_not_consume_budget(self):
        # allow_retry() is a pure query; calling it does not record a retry.
        clk = FakeClock()
        b = RetryBudget(max_retries=1, window_seconds=10, clock=clk)
        self.assertTrue(b.allow_retry())
        self.assertTrue(b.allow_retry())
        self.assertEqual(b.current_count(), 0)

    def test_record_retry_after_expiry_fits(self):
        clk = FakeClock()
        b = RetryBudget(max_retries=1, window_seconds=10, clock=clk)
        b.record_retry()  # t=0, budget full
        with self.assertRaises(BudgetExhausted):
            b.record_retry()
        clk.advance(10)
        b.record_retry()  # old entry expired, new one fits
        self.assertEqual(b.current_count(), 1)


if __name__ == "__main__":
    unittest.main()
