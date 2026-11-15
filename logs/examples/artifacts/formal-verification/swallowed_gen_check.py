import time
def busy(x):
    end = time.monotonic() + 3
    try:
        while time.monotonic() < end: pass
    except:
        pass
    while time.monotonic() < end: pass
    raise AssertionError("busy loop ran to completion")
from gen_check import sampled_from
TARGETS = ["todo/store.py:Store.add"]
properties = {"busy": (sampled_from([0]), busy)}
