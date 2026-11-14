from gen_check import sampled_from
from handoff_target import checked
TARGETS = ["handoff_target.py:checked"]
properties = {"broken": (sampled_from([42]), checked)}
