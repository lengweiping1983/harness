"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["move"]("queued", "start") == "running", "Start before completion."
assert work["move"]("running", "finish") == "completed", "Finish a running task."
try:
    work["move"]("queued", "finish")
except ValueError:
    pass
else:
    raise AssertionError("Reject impossible transitions.")

print("CHECK PASS")
