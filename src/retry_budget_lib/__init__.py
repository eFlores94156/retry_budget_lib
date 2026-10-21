"""retry_budget_lib: a shared retry budget so a failing dependency cannot amplify load."""

from retry_budget_lib.core import RetryBudget, BudgetExhausted

__all__ = ["RetryBudget", "BudgetExhausted"]
