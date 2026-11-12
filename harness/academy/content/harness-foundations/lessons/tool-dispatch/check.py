"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

execute = work.get("execute")
assert execute("add", 20, 22) == 42, "Dispatch the known tool."
try:
    execute("missing", 1, 2)
except ValueError:
    pass
else:
    raise AssertionError("Reject unknown tools instead of guessing.")

print("CHECK PASS")
