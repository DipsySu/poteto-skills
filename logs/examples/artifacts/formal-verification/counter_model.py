import time
def init(): return 0
def actions(s):
    time.sleep(0.001)
    return [("inc", s + 1)]
invariants = {"nonnegative": lambda s: s >= 0}
DEPTH = MAX_STATES = 10**9
