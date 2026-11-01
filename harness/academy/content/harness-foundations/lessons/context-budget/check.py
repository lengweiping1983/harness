"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assemble = work["assemble"]
assert assemble("task", "extra", 4) == "task", "Keep the task when optional content does not fit."
assert assemble("task", "extra", 20) == "taskextra", "Include optional context when it fits."

print("CHECK PASS")
