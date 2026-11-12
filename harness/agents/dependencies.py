"""成员依赖校验：只能引用执行成员，并且不能形成环。"""
from typing import Any, Sequence


def validate_dependencies(members: Sequence[Any]) -> None:
    """依赖可为空；拒绝无效引用及环，执行前就能发现错误。"""
    workers = {member.name: member for member in members if member.role != 'lead'}
    for member in members:
        deps = member.depends_on
        if not isinstance(deps, (tuple, list)) or any(not isinstance(dep, str) for dep in deps):
            raise ValueError('depends_on')
        if len(deps) != len(set(deps)) or (member.role == 'lead' and deps):
            raise ValueError('depends_on')
        if any(dep not in workers or dep == member.name for dep in deps):
            raise ValueError('depends_on')
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ValueError('depends_on')
        if name in visited:
            return
        visiting.add(name)
        for dep in workers[name].depends_on:
            visit(dep)
        visiting.remove(name)
        visited.add(name)

    for name in workers:
        visit(name)
