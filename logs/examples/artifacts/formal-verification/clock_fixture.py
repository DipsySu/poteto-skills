import review_clock as clock
def init(): return 0
def actions(s): return []
invariants = {"ok": lambda s: True}
def crash(s): raise ValueError("broken mutant")
def slow(s):
    clock.advance(20)
    return []
mutants = {"crash": crash, "slow": slow}
mutants = {"crash": crash}
