"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["safe_path"]("notes.txt") == "notes.txt", "Allow an ordinary relative path."
for raw in ("../secret.txt", "/private.txt", "src/../../secret"):
    try:
        work["safe_path"](raw)
    except ValueError:
        continue
    raise AssertionError("Reject paths outside the workspace.")

print("CHECK PASS")
