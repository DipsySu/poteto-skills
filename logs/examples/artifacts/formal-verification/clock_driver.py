import contextlib, importlib.util, io, json, sys, types
checker, fixture, *argv = sys.argv[1:]
spec = importlib.util.spec_from_file_location("gen_check" if "gen_check" in checker else "bounded_check", checker)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
clock = types.ModuleType("review_clock")
clock.now, clock.events = 0.0, []
def advance(now, tick=True):
    clock.now = now
    if tick: clock.limits.tick(None, None)
clock.advance = advance
sys.modules[clock.__name__] = clock
mod.time = types.SimpleNamespace(monotonic=lambda: clock.now, time=lambda: clock.now)
class ManualLimits(mod.Limits):
    def __enter__(self):
        self.active = True
        clock.limits = self
        return self
    def __exit__(self, *exc):
        return False
mod.Limits = ManualLimits
out, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    code = mod.main([fixture, *argv])
print(json.dumps({"code": code, "stdout": out.getvalue(), "stderr": err.getvalue(), "events": clock.events}))
