from gen_check import Generator
from review_shrink_target import confirmed
TARGETS = ["review_shrink_target.py:confirmed"]
def check(x):
    while x == 0: pass
    return confirmed(x)
properties = {"broken": (Generator(lambda rng: 100, lambda x: iter([0])), check)}
