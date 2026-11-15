from gen_check import Generator
import time
from todo_props import reset, round_trip
TARGETS = ["todo/store.py:Store.add"]
calls = 0
STOP_AFTER = 1
def lead(x):
    global calls
    calls += 1
    if calls == STOP_AFTER: time.sleep(1)
def later(x):
    round_trip("valid")
    return False
gen = Generator(lambda rng: rng.randrange(2**60), lambda x: [])
properties = {"lead": (gen, lead), "later": (gen, later)}
