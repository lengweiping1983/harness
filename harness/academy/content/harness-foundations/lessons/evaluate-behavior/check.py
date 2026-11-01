"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["evaluate"](42, True) == {"task_result": True, "permission_respected": True}, "Score independent dimensions."
assert work["evaluate"](42, False)["permission_respected"] is False, "A correct answer cannot hide a permission violation."
assert work["evaluate"](0, True)["task_result"] is False, "Correct permissions do not prove task success."

print("CHECK PASS")
