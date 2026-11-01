"""独立检查本课行为，不修改学生源码。"""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import runpy

stream = StringIO()
with redirect_stdout(stream):
    work = runpy.run_path(str(Path.cwd() / "main.py"))
output = stream.getvalue()

messages = work.get("messages")
assert isinstance(messages, list) and len(messages) >= 2, "Keep a conversation with at least two messages."
assert messages[0]["role"] == "user" and messages[1]["role"] == "assistant", "Keep question then answer."
assert all(isinstance(item.get("content"), str) for item in messages), "Message contents are text."
assert messages[-1]["content"] in output, "Display the actual latest message."

print("CHECK PASS")
