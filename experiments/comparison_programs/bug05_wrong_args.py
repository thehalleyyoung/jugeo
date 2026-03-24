"""Bug 05: Calling function with wrong number of arguments."""


def greet(name: str, greeting: str = "Hello") -> str:
    return f"{greeting}, {name}!"


# Wrong argument count: too many positional args
msg = greet("Alice", "Hi", "Extra")

# Wrong argument count: missing required arg
msg2 = greet()
