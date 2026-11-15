from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
_values = [-1, -2, 60]
def draw(rng):
    return _values.pop(0) if _values else 0
def check(x):
    if x < 0:
        reject()
    round_trip("valid")
    assert x <= 50, "above 50"
properties = {"rare": (Generator(draw, lambda x: []), check)}
