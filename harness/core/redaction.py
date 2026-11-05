"""敏感文本与凭据引用的唯一脱敏实现。

审批展示、计划正文检查与跨代理上下文继承都必须使用同一套判定，
否则同一份凭据会在某一条路径上原样外泄。本模块只提供纯函数：

* :func:`is_sensitive_path` / :func:`is_sensitive_reference` —— 路径/引用是否指向本机凭据；
* :func:`contains_sensitive_literal` —— 正文是否含明文凭据形态；
* :func:`redact_sensitive` / :func:`redact_text` —— 结构化与纯文本脱敏，
  后者同时返回命中次数，供调用方如实报告"已脱敏"而不是静默改写；
* :func:`contains_sensitive_value` —— 递归判定工具参数是否触及敏感边界。

脱敏只作用于展示与继承副本，绝不改变真实工具参数、执行指纹或落盘原件。
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

#: 明显的本机凭据文件名；示例模板不在此列。
SENSITIVE_BASENAMES = {
    '.env', '.env.local', '.env.production', '.env.development', '.env.test',
    '.git-credentials', '.netrc', '.npmrc', '.pypirc',
    'credentials.json', 'credentials.yaml', 'credentials.yml',
    'oauth-credentials.json', 'connection-secrets.json', 'secrets.json',
    'secrets.yaml', 'secrets.yml', 'token.json', 'tokens.json',
    'secret.txt', 'secrets.txt', 'password.txt', 'passwords.txt',
    'credentials.txt', 'auth.json', 'auth.yaml', 'auth.yml',
    'id_rsa', 'id_ed25519', 'id_ecdsa', 'private_key', 'privatekey',
}
#: 凭据常见后缀。
SENSITIVE_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.jks', '.kdbx', '.secret', '.credentials')
#: 带这些标记的文件是模板而不是真实凭据。
SAFE_EXAMPLE_MARKERS = ('.example', '.sample', '.template', '.dist')

#: 明文凭据形态；命中即视为敏感正文。
SENSITIVE_LITERAL_RE = re.compile(
    r"(?:-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie|credential|token)\b[\"']?\s*[:=]\s*[\"']?(?!<redacted>|\[redacted\])[^\s,;}\]]{4,}|"
    r"\b(?:Bearer|Basic)\s+(?!<redacted>|\[redacted\])[A-Za-z0-9._~+/=-]{4,}|"
    r"(?<![\w-])(?:sk-[A-Za-z0-9_-]{9,}|gh[pousr]_[A-Za-z0-9_]{13,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{11,})(?![\w-])|"
    r"(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?![\w-]))",
    re.IGNORECASE,
)

#: 云凭据选择器类环境变量名；它们指向真实凭据文件，因此与密钥同级处理。
_CREDENTIAL_ENV_NAMES = (
    'AWS_SHARED_CREDENTIALS_FILE|AWS_CONFIG_FILE|AWS_PROFILE|BOTO_CONFIG|AZURE_CONFIG_DIR'
    '|AZURE_AUTH_LOCATION|CLOUDSDK_CONFIG|NPM_CONFIG_USERCONFIG|PIP_CONFIG_FILE'
    '|PIP_INDEX_URL|PIP_EXTRA_INDEX_URL|DOCKER_CONFIG|GIT_ASKPASS|GIT_SSH_COMMAND'
)
_ASSIGNMENT_NAMES = (
    'api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization'
    '|cookie|private[_-]?key|credential|token'
)

SENSITIVE_FIELD_RE = re.compile(
    rf"(?:{_ASSIGNMENT_NAMES}|{_CREDENTIAL_ENV_NAMES.lower()})", re.IGNORECASE)
SENSITIVE_QUOTED_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_ASSIGNMENT_NAMES})\b["']?\s*[:=]\s*)"(?:\\.|[^"\\])*"''')
SENSITIVE_SINGLE_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_ASSIGNMENT_NAMES})\b["']?\s*[:=]\s*)'(?:\\.|[^'\\])*' '''.rstrip())
SENSITIVE_UNQUOTED_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_ASSIGNMENT_NAMES})\b["']?\s*[:=]\s*)(?!["'])([^\s,;}}\]]+)''')
SENSITIVE_ASSIGNMENT_MARKER_RE = re.compile(
    rf'''(?i)\b(?:{_ASSIGNMENT_NAMES})\b["']?\s*[:=]\s*["']?(?!<redacted>|\[redacted\])[^\s,;}}\]]+''')
SENSITIVE_ENV_QUOTED_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_CREDENTIAL_ENV_NAMES})\b\s*=\s*)"(?:\\.|[^"\\])*"''')
SENSITIVE_ENV_SINGLE_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_CREDENTIAL_ENV_NAMES})\b\s*=\s*)'(?:\\.|[^'\\])*' '''.rstrip())
SENSITIVE_ENV_UNQUOTED_ASSIGNMENT_RE = re.compile(
    rf'''(?i)(\b(?:{_CREDENTIAL_ENV_NAMES})\b\s*=\s*)(?!["'])([^\s,;}}\]]+)''')
SENSITIVE_ENV_REFERENCE_RE = re.compile(
    rf'''(?i)(?<![A-Z0-9_])(?:{_CREDENTIAL_ENV_NAMES})(?![A-Z0-9_])''')
AUTH_SCHEME_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[\"']?[^\s,;}\]]+[\"']?")
PRIVATE_KEY_RE = re.compile(r"(?is)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----")
SECRET_TOKEN_RE = re.compile(
    r"(?i)(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]{12,}"
    r"|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,})(?![\w-])")
JWT_RE = re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}(?![\w-])")

#: 文本脱敏按此顺序执行；顺序影响嵌套形态的归一结果，不得随意调整。
_TEXT_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], Any], ...] = (
    (PRIVATE_KEY_RE, '[redacted-private-key]'),
    (AUTH_SCHEME_RE, lambda match: f'{match.group(1)} [redacted]'),
    (SECRET_TOKEN_RE, '[redacted-token]'),
    (JWT_RE, '[redacted-token]'),
    (SENSITIVE_ENV_QUOTED_ASSIGNMENT_RE, r'\1"[redacted]"'),
    (SENSITIVE_ENV_SINGLE_ASSIGNMENT_RE, r"\1'[redacted]'"),
    (SENSITIVE_ENV_UNQUOTED_ASSIGNMENT_RE, r'\1[redacted]'),
    (SENSITIVE_QUOTED_ASSIGNMENT_RE, r'\1"[redacted]"'),
    (SENSITIVE_SINGLE_ASSIGNMENT_RE, r"\1'[redacted]'"),
    (SENSITIVE_UNQUOTED_ASSIGNMENT_RE, r'\1[redacted]'),
)


def is_sensitive_reference(value: Any) -> bool:
    """判断文件/搜索引用是否可能暴露本地凭据。"""
    if not isinstance(value, str) or not value.strip():
        return False
    # 同时拆开命令/标题中的空白和常见标点，避免 `cat .env` 或
    # `--path=.env` 通过只按目录分隔符切分的检查。
    parts = [part for part in re.split(r'''[\\/\s"'`;,(){}\[\]=:]+''', value.strip().lower()) if part]
    for part in parts:
        if any(marker in part for marker in SAFE_EXAMPLE_MARKERS):
            continue
        if part in {'.ssh', '.aws', '.azure'}:
            return True
        if part in SENSITIVE_BASENAMES or part.endswith(SENSITIVE_SUFFIXES):
            return True
    return False


def is_sensitive_path(value: str) -> bool:
    """检查完整路径分量，不将源码的等号、括号或成员运算拆成路径。"""
    for part in re.split(r'[\\/]', value.lower()):
        if any(marker in part for marker in SAFE_EXAMPLE_MARKERS):
            continue
        if part in {'.ssh', '.aws', '.azure'} or part in SENSITIVE_BASENAMES or part.endswith(SENSITIVE_SUFFIXES):
            return True
    return False


def contains_sensitive_literal(value: Any) -> bool:
    """识别正文中的常见明文凭据形态，不保存或展示命中正文。"""
    return isinstance(value, str) and bool(SENSITIVE_LITERAL_RE.search(value))


def redact_text(value: str) -> tuple[str, int]:
    """脱敏纯文本并返回命中次数；调用方据此如实报告，不静默改写。"""
    hits = 0
    for pattern, replacement in _TEXT_SUBSTITUTIONS:
        value, count = pattern.subn(replacement, value)
        hits += count
    return value, hits


def redact_sensitive(value: Any) -> Any:
    """递归脱敏结构化数据，仅用于展示与继承副本。"""
    if isinstance(value, dict):
        return {key: '[redacted]' if SENSITIVE_FIELD_RE.search(str(key)) else redact_sensitive(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)[0]
    return value


def redact_path(value: Any) -> Any:
    """隐藏明显凭据目录，保留普通工作目录的可核对性。"""
    if not isinstance(value, str):
        return value
    return '[redacted-path]' if is_sensitive_path(value) else value


def contains_sensitive_value(value: Any, *, public_fields: frozenset[str] = frozenset(),
                             reference_checks: Mapping[str, Callable[[str], bool]] | None = None) -> bool:
    """仅当前层使用公开字段名和字符串引用检查，秘密及嵌套参数始终扫描。"""
    if isinstance(value, dict):
        return any((key not in public_fields and SENSITIVE_FIELD_RE.search(str(key)))
                   or _contains_sensitive_argument(item, (reference_checks or {}).get(key, is_sensitive_reference))
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_sensitive_value(item) for item in value)
    if isinstance(value, str):
        return _contains_sensitive_argument(value, is_sensitive_reference)
    return False


def _contains_sensitive_argument(value: Any, reference_check: Callable[[str], bool]) -> bool:
    """引用检查仅作用于当前字符串，结构化值恢复默认递归边界。"""
    if not isinstance(value, str):
        return contains_sensitive_value(value)
    return (contains_sensitive_literal(value)
            or bool(SENSITIVE_ASSIGNMENT_MARKER_RE.search(value))
            or bool(SENSITIVE_ENV_REFERENCE_RE.search(value))
            or reference_check(value))


__all__ = [
    'AUTH_SCHEME_RE', 'JWT_RE', 'PRIVATE_KEY_RE', 'SAFE_EXAMPLE_MARKERS',
    'SECRET_TOKEN_RE', 'SENSITIVE_ASSIGNMENT_MARKER_RE', 'SENSITIVE_BASENAMES',
    'SENSITIVE_ENV_REFERENCE_RE', 'SENSITIVE_FIELD_RE', 'SENSITIVE_LITERAL_RE',
    'SENSITIVE_SUFFIXES', 'contains_sensitive_literal', 'contains_sensitive_value',
    'is_sensitive_path', 'is_sensitive_reference', 'redact_path', 'redact_sensitive', 'redact_text',
]
