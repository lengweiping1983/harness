"""系统技能与用户安装技能的统一路径规则。"""
from pathlib import Path

from ..core.errors import ValidationError
from ..i18n import tr


SYSTEM_SKILL_ROOT = Path(__file__).resolve().parents[2] / '.harness' / 'skills'


def user_skill_root(home: str | Path | None = None) -> Path:
    """默认与 ~/.harness 并列；自定义运行根保持用户数据隔离。"""
    base = Path(home).expanduser() if home is not None else Path.home() / '.harness'
    agent = base.parent / '.agents' if base.name == '.harness' else base / '.agents'
    return agent / 'skills'


def validate_installation(kind: str, scope: str) -> None:
    """安装和卸载只接受当前声明的类型与作用域，不回退用户目录。"""
    if not isinstance(kind, str) or kind not in ('skill', 'agent'):
        raise ValidationError(tr('plugin.installation.invalidKind', value=repr(kind)))
    if not isinstance(scope, str) or scope not in ('user', 'project'):
        raise ValidationError(tr('plugin.installation.invalidScope', value=repr(scope)))


def installation_path(value: str | Path | None, field: str) -> Path | None:
    """只在省略时采用默认目录，显式空值或非路径类型不能回退。"""
    if value is None:
        return None
    if not isinstance(value, (str, Path)) or isinstance(value, str) and not value.strip():
        raise ValidationError(tr('plugin.installation.invalidPath', field=field))
    try:
        return Path(value).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise ValidationError(tr('plugin.installation.invalidPath', field=field)) from error


def installation_root(kind: str, scope: str, *, cwd: str | Path | None = None, home: str | Path | None = None) -> Path:
    """用户安装技能进入 .agents，Agent沿用独立的 .harness 目录。"""
    validate_installation(kind, scope)
    cwd = installation_path(cwd, 'cwd')
    home = installation_path(home, 'home')
    if scope == 'project':
        return (Path(cwd) if cwd is not None else Path.cwd()) / ('.agents' if kind == 'skill' else '.harness') / ('skills' if kind == 'skill' else 'agents')
    if kind == 'skill':
        return user_skill_root(home)
    return (Path(home).expanduser() if home is not None else Path.home() / '.harness') / 'agents'
