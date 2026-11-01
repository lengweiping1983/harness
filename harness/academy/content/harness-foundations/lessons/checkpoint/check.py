"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["checkpoint"]["files"]["agent.py"] == "print(42)", "The checkpoint must survive later edits."
assert work["state"]["files"]["agent.py"] == "broken code", "Keep the experimental state distinct."
assert work["restored"] == work["checkpoint"] and work["restored"] is not work["checkpoint"], "Restore an independent copy."

print("CHECK PASS")
