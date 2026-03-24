"""Bug 01: Wrong type passed to function."""
from typing import List


def sum_values(nums: List[int]) -> int:
    total = 0
    for n in nums:
        total += n
    return total


# Type error: passing a list of strings instead of ints
result = sum_values(["a", "b", "c"])
print(result)
