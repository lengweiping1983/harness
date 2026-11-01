"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert callable(work.get("model_fixture")) and callable(work.get("run")), "Keep the model boundary and loop visible."
seen = []
real_model = work["model_fixture"]
def observe(messages):
    seen.append([dict(message) for message in messages])
    return real_model(messages)
work["run"].__globals__["model_fixture"] = observe
answer = work["run"]()
assert answer is not None and len(seen) == 2, "Use two requests: propose an action, then observe it."
assert seen[0][-1]["role"] == "user" and seen[1][-1]["role"] == "tool", "The next request must receive the tool observation."
assert answer == seen[1][-1]["content"], "The answer follows the actual observation."

print("CHECK PASS")
