"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

import json

result = work["run"]()
assert json.loads(output) == result, "Export the answer and events as JSON for the request theater."
assert result["answer"] == 42, "Complete the task using the actual observation."
assert result["events"] == ["model/request", "tool/result", "model/request", "turn/end"], "Retain a trace of request, tool, feedback, and completion."
def denied(messages):
    return {"tool": "delete", "a": 20, "b": 22}
work["run"].__globals__["model_fixture"] = denied
try:
    work["run"]()
except ValueError:
    pass
else:
    raise AssertionError("Unknown tools must not be executed.")

calls = []
def always_add(messages):
    calls.append([dict(message) for message in messages])
    return {"tool": "add", "a": 20, "b": 22}
work["run"].__globals__["model_fixture"] = always_add
try:
    work["run"]()
except RuntimeError as error:
    assert str(error) == "budget-exhausted", "Report the exhausted budget explicitly."
else:
    raise AssertionError("Stop when the model keeps asking for work.")
assert len(calls) == 2, "Make exactly the allowed number of requests before stopping."

print("CHECK PASS")
