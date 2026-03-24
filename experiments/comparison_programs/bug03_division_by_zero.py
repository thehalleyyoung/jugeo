"""Bug 03: Potential division by zero (no guard)."""


def safe_divide(a: float, b: float) -> float:
    return a / b  # b could be 0


def average(values: list) -> float:
    return sum(values) / len(values)  # len could be 0


print(safe_divide(10, 0))
print(average([]))
