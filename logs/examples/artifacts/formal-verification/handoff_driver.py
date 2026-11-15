import importlib.util, signal, sys
checker, *argv = sys.argv[1:]
spec = importlib.util.spec_from_file_location("gen_check", checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
original = mod.run_property
def interrupted_return(*args, **kwargs):
    result = original(*args, **kwargs)
    assert result[0]["result"] == "fail" and result[0]["counterexample"] == "42"
    limits = args[7]
    limits.deadline = limits.begun - 1
    signal.raise_signal(signal.SIGALRM)  # exactly after return, before main can write back
    raise AssertionError("injected SIGALRM was not delivered")
mod.run_property = interrupted_return
raise SystemExit(mod.main(argv))
