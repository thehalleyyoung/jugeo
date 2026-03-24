"""Bug 02: Function missing return on some paths."""
from typing import Optional


def find_first_positive(nums: list) -> int:
    for n in nums:
        if n > 0:
            return n
    # Missing return / implicit None on this path — declared return type is int


def classify(x: int) -> str:
    if x > 0:
        return "positive"
    elif x < 0:
        return "negative"
    # Missing return for x == 0 case
