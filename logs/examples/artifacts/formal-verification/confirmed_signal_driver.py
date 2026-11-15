import importlib.util, json, signal, sys
checker, fixture, phase, *argv = sys.argv[1:]
name = "gen_check" if "gen_check" in checker else "bounded_check"
spec = importlib.util.spec_from_file_location(name, checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[name] = mod
spec.loader.exec_module(mod)
fired = False
def inject(frame, event, arg):
    global fired
    data = frame.f_locals
    returned = ((name == "gen_check" and phase == "initial" and frame.f_code.co_name == "run_property"
                 and data.get("status") == "fail" and data["result"]["result"] != "fail")
                or (name == "gen_check" and phase == "shrink" and frame.f_code.co_name == "reduce_failure"
                    and data.get("status") == "fail" and data.get("candidate") == 7)
                or (name == "gen_check" and phase == "shrink_handoff" and frame.f_code.co_name == "run_property"
                    and data.get("value") == 7 and "attempts" in data)
                or (name == "bounded_check" and frame.f_code.co_name == "check"
                    and data.get("ok") is False))
    if event == "line" and returned and not fired:
        fired = True
        limits = data["limits"]
        limits.deadline = limits.begun - 1
        signal.raise_signal(signal.SIGALRM)
    return inject
sys.settrace(inject)
try:
    code = mod.main([fixture, *argv])
finally:
    sys.settrace(None)
assert fired, "return-to-record signal injection did not run"
raise SystemExit(code)
