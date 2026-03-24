"""Bug 10: Mutating container while iterating + mutable default arg."""
from typing import List


def remove_negatives(nums: List[int], cache: List[int] = []) -> List[int]:
    """Remove negative numbers by mutating the list while iterating."""
    for n in nums:
        if n < 0:
            nums.remove(n)  # mutation while iterating
        cache.append(n)
    return nums


def drop_keys(d: dict, bad_keys: list) -> dict:
    for key in d:          # iterating over dict
        if key in bad_keys:
            del d[key]     # mutation while iterating
    return d


data = [1, -2, 3, -4, 5]
print(remove_negatives(data))
