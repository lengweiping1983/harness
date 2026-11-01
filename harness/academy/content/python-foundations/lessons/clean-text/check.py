"""独立核对本课行为；不修改学生文件。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

assert isinstance(work.get("name"), str) and work["name"].strip(), "Keep a nonempty input name."
assert work.get("clean_name") == work["name"].strip(), "Remove whitespace from both ends."
assert work.get("greeting") == f"Hello, {work["clean_name"]}!", "Build the greeting from clean_name."
assert work["greeting"] in output, "Print your greeting."

print("CHECK PASS")
