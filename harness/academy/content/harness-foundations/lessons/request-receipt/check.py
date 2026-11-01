"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

submit = work["submit"]
assert submit("test", 9) == 9 and submit("test", 9) == 9, "Repeated identical input reuses the receipt."
try:
    submit("test", 10)
except ValueError:
    pass
else:
    raise AssertionError("Reject conflicting input for the same request.")

print("CHECK PASS")
