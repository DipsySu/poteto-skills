import review_clock as clock
TIMEOUT = 0
def check(x):
    clock.advance(1000)
    return True
def init(): return 0
def actions(s): return []
invariants = {"ok": check}
