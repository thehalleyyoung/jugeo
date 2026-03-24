"""Bug 06: Accessing non-existent attribute."""
from dataclasses import dataclass


@dataclass
class Point:
    x: float
    y: float


p = Point(1.0, 2.0)
# Attribute does not exist on Point
print(p.z)
print(p.magnitude)

# Method doesn't exist
result = p.to_polar()
