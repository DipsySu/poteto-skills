from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
def check(x):
    round_trip("valid")
    raise AssertionError("violation")
def shrink(x): raise RuntimeError("shrink")
properties = {"broken": (Generator(lambda rng: 1, shrink), check)}
