from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
def check(x):
    round_trip("valid")
    raise AssertionError("violation")
def reset(): raise RuntimeError("reset")
properties = {"broken": (ints(), check)}
