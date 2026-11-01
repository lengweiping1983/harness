"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

model = work.get("model_fixture")
assert callable(model), "Provide the model boundary as a function."
first, second = model("one"), model("two")
assert isinstance(first, str) and isinstance(second, str), "The model boundary returns text."
assert "one" in first and "two" in second and first != second, "Use the current input instead of a stale question."

print("CHECK PASS")
