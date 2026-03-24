"""Bug 08: Using Optional value without None check."""
from typing import Optional


def get_user_name(user_id: int) -> Optional[str]:
    if user_id == 1:
        return "Alice"
    return None


name = get_user_name(99)
# name could be None here — calling upper() without None check
print(name.upper())
print(name.split())
