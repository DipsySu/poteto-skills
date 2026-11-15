from gen_check import sampled_from
import time
from todo_props import reset, round_trip
TARGETS = ["todo/store.py:Store.add"]
def check(x):
    time.sleep(0.1)
    round_trip("valid")
properties = {"ok": (sampled_from([0]), check)}
