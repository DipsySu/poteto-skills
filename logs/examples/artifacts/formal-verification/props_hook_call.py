from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
def setup():
    reset()
    round_trip("setup")
properties = {"ok": (ints(), lambda x: None)}
