import time
def busy(x):
    end = time.monotonic() + 3
    try:
        while time.monotonic() < end: pass
    except:
        pass
    while time.monotonic() < end: pass
    raise AssertionError("busy loop ran to completion")
def init(): return 0
def actions(s): return []
invariants = {"busy": busy}
