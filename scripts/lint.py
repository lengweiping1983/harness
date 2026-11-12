"""轻量代码检查：语法编译和未使用的 import。

使用内置检查逻辑，但能抓住重构时最容易引入的
"删了代码忘了删 import"这类问题。
"""

from __future__ import annotations

import ast
import compileall
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def check_syntax() -> int:
    """编译所有模块，语法错误会在这里暴露。"""
    ok = all([compileall.compile_dir(str(ROOT / name), quiet=2, force=True)
              for name in ("harness", "tests", "scripts")])
    print("语法检查：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


def check_unused_imports() -> int:
    """找出导入后从未被引用的名字。

    只统计语法树中的读取、导出和字符串类型标注，注释不能掩盖未使用导入。
    """
    problems: list[str] = []
    for path in sorted(path for name in ("harness", "tests", "scripts")
                       for path in (ROOT / name).rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        used = {node.id for node in ast.walk(tree)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                try:
                    annotation = ast.parse(node.value, mode='eval')
                except (SyntaxError, ValueError):
                    continue
                used.update(item.id for item in ast.walk(annotation) if isinstance(item, ast.Name))
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and target.id == '__all__' for target in targets):
                    used.update(item.value for item in ast.walk(node.value)
                                if isinstance(item, ast.Constant) and isinstance(item.value, str))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name in ("*", "annotations"):
                    continue
                if name not in used:
                    rel = path.relative_to(ROOT)
                    problems.append(f"  {rel}:{node.lineno} 未使用的 import：{name}")
    for line in problems:
        print(line)
    print(f"未使用 import：{len(problems)}")
    return 1 if problems else 0


def main() -> int:
    return check_syntax() | check_unused_imports()


if __name__ == "__main__":
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
