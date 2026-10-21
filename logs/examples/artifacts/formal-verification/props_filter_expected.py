from gen_check import *
TARGETS = ["todo/store.py:Store.add"]
from todo_props import reset, round_trip
def check(x):
    round_trip("valid")
    if x: reject()
properties = {"filtered": (sampled_from([True]), check)}
examples = {"filtered": [False]}
