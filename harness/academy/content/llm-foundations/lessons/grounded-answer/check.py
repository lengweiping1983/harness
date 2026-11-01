"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

for value in (0, False, "", "09:00"):
    document = {"fact": value}
    assert work["answer_from"](document, "fact") == {"status": "supported", "answer": value, "source": "fact"}, "Preserve present values including zero and false."
    assert document == {"fact": value}, "Preserve source evidence."
assert work["answer_from"]({}, "missing") == {"status": "unknown", "answer": None, "source": None}, "Do not invent missing evidence."

print("CHECK PASS")
