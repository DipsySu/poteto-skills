def init(): return 0
def actions(s): return [("loop",s)]
invariants={"ok":lambda s:True}
mutants={"dead":lambda s:[]}
