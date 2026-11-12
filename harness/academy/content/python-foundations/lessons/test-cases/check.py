"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

work["check_validator"](work["validate_minutes"])
def passthrough(value):
    return value
def always_zero(value):
    return 0
def reject_zero(value):
    if type(value) is not int or value <= 0:
        raise ValueError("minutes")
    return value
for broken in (passthrough, always_zero, reject_zero):
    try:
        work["check_validator"](broken)
    except (AssertionError, ValueError):
        continue
    raise AssertionError("Your tests must expose each broken validator.")

print("CHECK PASS")
