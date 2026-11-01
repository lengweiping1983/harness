"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert isinstance(work["history"], list) and len(work["history"]) == 4, "Preserve full history."
assert work["context"] and work["context"] == work["history"][-len(work["context"]):], "Select a current tail without destroying history."
assert work["context"] is not work["history"], "Keep the projection separate."

print("CHECK PASS")
