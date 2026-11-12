"""技能发现的目录边界，与可迁移记忆的共享根目录分开。"""
from pathlib import Path


def checkout_root(cwd: str | Path) -> Path:
    """取最近 Git 边界；非仓库只使用当前目录，不扫描无关父目录。"""
    base = Path(cwd).resolve()
    return next((parent for parent in (base, *base.parents) if (parent / '.git').exists()), base)


def project_directories(cwd: str | Path, fallback_root: str | Path | None = None) -> list[Path]:
    """从工作目录向上发现技能；空目录也明确阻止主工作区回退。"""
    base = Path(cwd).resolve()
    root = checkout_root(base)
    directories = []
    current = base
    while True:
        directories.append(current)
        if current == root:
            break
        current = current.parent
    configured = any((directory / kind / 'skills').is_dir()
                     for directory in directories for kind in ('.agents', '.harness'))
    if not configured and fallback_root is not None:
        fallback = Path(fallback_root).resolve()
        if fallback != root:
            directories.append(fallback)
    return directories
