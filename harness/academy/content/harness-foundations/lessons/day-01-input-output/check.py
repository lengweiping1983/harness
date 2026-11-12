"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert isinstance(work.get("question"), str) and work["question"].strip(), "Keep a non-empty question."
assert isinstance(work.get("reply"), str) and work["reply"].strip(), "Keep a non-empty fixed reply."
assert work["question"] in output and work["reply"] in output, "Display both input and output."

print("CHECK PASS")
