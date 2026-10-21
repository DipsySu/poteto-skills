from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
reset()
round_trip("imported")
properties = {"ok": (ints(), lambda x: None)}
