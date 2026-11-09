from gen_check import text
from todo.text import strip_leading
TARGETS = ["todo/text.py:strip_leading"]
properties = {"strip_leading": (text(" ab", max_size=8), lambda t: strip_leading(t) == t.lstrip(" ")),
              "no_leading_space": (text("ab", max_size=8), lambda t: strip_leading(t) == t)}
