"""The Lean model uses Nat, so the intended input domain is nonnegative integers."""


def increment(n: int) -> int:
    return n + 1


def buggy_increment(n: int) -> int:
    return n + 2
