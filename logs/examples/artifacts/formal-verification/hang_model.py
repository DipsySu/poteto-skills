def init(): return 0
def actions(s):
    while s == 1:
        pass
    return [("step", s + 1)] if s < 3 else []
invariants = {"ok": lambda s: True}
