from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
def check(x):
    round_trip("valid")
    raise AssertionError("violation")
def draw(rng): raise RuntimeError("draw")
properties = {"broken": (Generator(draw, lambda x: []), check)}
