from gen_check import Generator
import signal
from todo_props import reset, round_trip
TARGETS = ["todo/store.py:Store.add"]
def check(x):
    round_trip("valid")
    return x < 10
def shrink(x):
    if x == 100: yield 60
    signal.raise_signal(signal.SIGINT)
properties = {"broken": (Generator(lambda rng: 100, shrink), check)}
