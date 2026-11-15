class Value:
    def __repr__(self):
        while True: pass
from gen_check import Generator
from format_target import checked
TARGETS = ["format_target.py:checked"]
properties = {"broken": (Generator(lambda rng: Value(), lambda x: []), checked)}
