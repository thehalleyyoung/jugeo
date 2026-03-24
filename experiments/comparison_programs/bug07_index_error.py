"""Bug 07: Potential index out of bounds (mutable default also present)."""
from typing import List


def get_first_three(items: List[int], result: List[int] = []) -> List[int]:
    result.append(items[0])
    result.append(items[1])
    result.append(items[2])
    return result


def head(lst: list):
    return lst[0]  # May raise IndexError if lst is empty


print(get_first_three([1, 2]))  # Only 2 elements — IndexError at items[2]
print(head([]))
