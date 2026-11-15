import time
def init(): return 0
def actions(s): return [("inc", s + 1)]
invariants = {"below_two": lambda s: s < 2}
def describe(s):
    if s == 1: time.sleep(2)
    return f"state {s}"
