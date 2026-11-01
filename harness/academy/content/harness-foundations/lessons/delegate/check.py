"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert work["child_permissions"](["read"], ["read", "write"]) == ["read"], "Do not escalate parent permissions."
assert work["child_permissions"]([], ["write"]) == [], "No parent permission means no child permission."
assert work["child"]["task"], "Provide a concrete member task."

print("CHECK PASS")
