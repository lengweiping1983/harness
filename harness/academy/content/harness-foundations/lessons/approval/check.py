"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

perform = work["perform"]
assert perform("one", {"request_id": "one", "decision": "approve"}) == ["action-executed"], "Execute an explicitly approved action."
assert perform("two", {"request_id": "one", "decision": "approve"}) == [], "Do not borrow another request's approval."
assert perform("one", {"request_id": "one", "decision": "deny"}) == [], "A denial prevents execution."

print("CHECK PASS")
