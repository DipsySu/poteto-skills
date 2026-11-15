class Value:
    def __repr__(self):
        while True: pass
def init(): return Value()
def actions(s): return []
invariants = {"broken": lambda s: False}
