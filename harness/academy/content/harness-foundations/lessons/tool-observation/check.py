"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["messages"][-1]["role"] == "tool", "Label observations as tool results."
assert work["messages"][-1]["content"] == work["result"], "Return the actual result rather than stale text."
assert isinstance(work["result"], int), "This arithmetic tool returns a number."

print("CHECK PASS")
