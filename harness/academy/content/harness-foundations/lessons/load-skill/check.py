"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert isinstance(work["load_skill"]("summarize"), str), "Load a known instruction bundle."
try:
    work["load_skill"]("missing")
except ValueError:
    pass
else:
    raise AssertionError("Do not invent missing skill content.")

print("CHECK PASS")
