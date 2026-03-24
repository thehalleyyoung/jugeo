"""JuGeo probe: programs with patterns JuGeo's static analyzer detects.

JuGeo detects mutable default arguments and boolean literal comparisons
via AST-level obstruction analysis (theory2.tex Ch11).
"""


def collect_items(item, cache=[]):
    """Mutable default: cache=[] is shared across all calls."""
    cache.append(item)
    return cache


def append_batch(items, results={}):
    """Mutable default: results={} is shared across all calls."""
    for k, v in items:
        results[k] = v
    return results


def is_active(flag) -> bool:
    if flag == True:   # should use `is True` or just `if flag`
        return True
    if flag == False:  # should use `is False` or `not flag`
        return False
    return False


def process_items(data, accumulator=[]):
    """Another mutable default accumulator pattern."""
    accumulator.extend(data)
    return accumulator
