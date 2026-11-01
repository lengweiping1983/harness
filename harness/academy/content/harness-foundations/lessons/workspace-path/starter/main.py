"""第 19 天：只在自己的练习本上写。"""
from pathlib import PurePosixPath

def safe_path(raw):
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("outside-workspace")
    return path.as_posix()

print(safe_path("src/demo.py"))
