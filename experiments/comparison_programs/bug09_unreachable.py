"""Bug 09: Unreachable code after return / bool comparison bugs."""


def absolute_value(x: int) -> int:
    if x < 0:
        return -x
        print("computed negative")  # unreachable
    return x
    print("computed positive")  # unreachable


def is_valid(flag) -> bool:
    if flag == True:  # should use `is True` or just `if flag`
        return True
    if flag == False:  # should use `is False` or `not flag`
        return False
    return False
