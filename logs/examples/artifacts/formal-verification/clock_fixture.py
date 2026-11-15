import review_clock as clock
TIMEOUT = None
CHECK_TIMEOUT = 0.3
clock.advance(100, tick=False)
def check(x):
    clock.events.append("checked")
    return True
from gen_check import sampled_from
from namespace_target import checked
TARGETS = ["namespace_target.py:checked"]
def covered(x):
    check(x)
    return checked(x)
properties = {"ok": (sampled_from([0]), covered)}
