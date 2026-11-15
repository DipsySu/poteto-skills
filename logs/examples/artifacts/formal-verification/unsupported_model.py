import time
def init():
    time.sleep(0.1)
    return 0
def actions(s): return []
invariants = {"ok": lambda s: True}
