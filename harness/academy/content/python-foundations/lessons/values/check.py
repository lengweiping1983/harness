"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert isinstance(work.get("learner"), str) and work["learner"].strip(), "Give learner a nonempty name."
assert type(work.get("minutes")) is int and work["minutes"] > 0, "Use a positive whole number of minutes."
assert work["learner"] in output and str(work["minutes"]) in output, "Print both named values."

print("CHECK PASS")
