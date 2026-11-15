from gen_check import Generator
from confirmed_target import checked
TARGETS = ["confirmed_target.py:checked"]
properties = {"broken": (Generator(lambda rng: 42, lambda x: [7] if x == 42 else []), checked)}
