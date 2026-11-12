"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["events"] == ["read failed", "read again succeeded", "answer drafted"], "Keep original evidence."
assert isinstance(work["summary"], str) and work["summary"].strip(), "Provide a short summary."
assert work["context"]["source_count"] == len(work["events"]), "Keep the source relation explicit."

print("CHECK PASS")
