"""Bug 04: Using variable before assignment on some paths."""


def process(flag: bool) -> int:
    if flag:
        result = 42
    # result may be uninitialized when flag is False
    return result  # type: ignore


def compute(values: list) -> int:
    for v in values:
        if v > 0:
            last = v
    return last  # last may be unbound if values is empty or all <= 0
