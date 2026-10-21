# retry_budget_lib

A shared retry budget so a failing dependency cannot amplify load. A `RetryBudget` caps the number of retries permitted inside a rolling time window; all callers sharing one budget compete for the same allowance.

```python
from retry_budget_lib import RetryBudget, BudgetExhausted

budget = RetryBudget(max_retries=10, window_seconds=60)

def call_dependency(request):
    try:
        return do_work(request)
    except TransientError:
        try:
            budget.attempt_retry()
        except BudgetExhausted:
            raise
        return do_work(request)
```

## Why

When a downstream service degrades, unbounded retries turn each request into several: a primary attempt plus N retries. If every caller retries independently, the aggregate load on the dependency spikes and can take it down entirely. A retry budget makes the total retry volume an explicit, shared, bounded quantity rather than an emergent one.

The trade-off here is simplicity over sophistication. This library implements a sliding window backed by a timestamp deque: every call prunes entries older than the window. That is O(retries-in-window) per call, which is fine for the small windows and modest retry counts a budget implies. A token-bucket with a continuous refill rate would be cheaper but makes the current allowance a function of elapsed time, which is harder to reason about under load spikes and harder to test deterministically.

One awkward edge: `attempt_retry` is the recommended call because it checks and records atomically. If you use `allow_retry` and `record_retry` separately, you must call `record_retry` only when you actually issue a retry — a rejected retry (one where `allow_retry` returned False) must not be recorded, because it was never issued. The budget counts real work, not rejected attempts.

## Exports

- `RetryBudget(max_retries, window_seconds, clock=time.monotonic)` — the budget. `max_retries` is a non-negative int; `window_seconds` is a positive float; `clock` is an injectable callable returning seconds, so tests can drive time.
- `BudgetExhausted` — raised by `attempt_retry` and `record_retry` when the budget cannot accommodate another retry.

`RetryBudget` methods:

- `current_count() -> int` — retries currently inside the window.
- `allow_retry() -> bool` — True if a retry would fit; does not consume budget.
- `record_retry() -> None` — record one retry; raises `BudgetExhausted` if over budget.
- `attempt_retry() -> None` — atomically check and consume one unit of budget; raises `BudgetExhausted` if over.
- `reset() -> None` — clear all recorded retries.
