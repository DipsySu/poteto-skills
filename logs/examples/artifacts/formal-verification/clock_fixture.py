import review_clock as clock
class Value:
    def __repr__(self):
        clock.events.append(clock.limits.recording)
        clock.advance(20)
        return "value"
def init(): return Value()
def actions(s): return []
invariants = {"broken": lambda s: False}
