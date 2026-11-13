import contextlib, io, json, runpy, signal, sys, threading
checker, scenario, *argv = sys.argv[1:]
if scenario == "no_timer": del signal.setitimer
sys.argv = [checker, *argv]
out, err, codes = io.StringIO(), io.StringIO(), []
def run():
    try: runpy.run_path(checker, run_name="__main__")
    except SystemExit as e: codes.append(e.code)
with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
    if scenario == "worker":
        thread = threading.Thread(target=run)
        thread.start()
        thread.join()
    else: run()
print(json.dumps({"code": codes[0], "report": json.loads(out.getvalue()), "stderr": err.getvalue()}))
