"""按可信参数用途检查文件引用，不把源码成员访问当作文件路径。"""
from __future__ import annotations

import re
import shlex
from collections import deque

from .plan_mode import is_sensitive_path, is_sensitive_reference


_STRINGS = re.compile(r'''(?s)"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`''')
_SUBSTITUTIONS = re.compile(r'\$\((.*?)\)|`([^`]*)`', re.DOTALL)
_SHELLS = {'sh', 'bash', 'dash', 'zsh', 'ksh'}
_INTERPRETERS = {'python', 'python3', 'node', 'nodejs', 'pwsh', 'powershell'}
_DATA_INPUTS = {'cat', 'tee'}
_DATA_ARGUMENTS = {'echo', 'printf'}
_SEPARATORS = {';', ';;', '&', '&&', '|', '||', '(', ')'}


def _unquote(word: str) -> str:
    """只解除词法引号，不展开变量、命令或文件名。"""
    if len(word) >= 2 and word[0] == word[-1] and word[0] in {'"', "'"}:
        return word[1:-1]
    return word


def _path_word(word: str) -> bool:
    """只检查整个命令词及选项值，避免再次拆开源码中的等号和括号。"""
    word = _unquote(word)
    if '=' in word:
        word = _unquote(word.split('=', 1)[1])
    return is_sensitive_path(word)


def code_has_sensitive_reference(source: str) -> bool:
    """可执行源码检查字符串中的文件引用，成员名本身不构成文件访问。"""
    return any(is_sensitive_reference(match.group()[1:-1]) for match in _STRINGS.finditer(source))


def _expansion_has_reference(value: str, depth: int) -> bool:
    """未加引号的 heredoc 和双引号命令词仍会执行命令替换。"""
    for match in _SUBSTITUTIONS.finditer(value):
        expression = match.group(1) or match.group(2) or ''
        # 替换结果可能成为路径或被二次执行，不能沿用 echo/printf 的数据例外。
        if is_sensitive_reference(expression) or shell_has_sensitive_reference(expression, depth=depth + 1):
            return True
    return False


def shell_has_sensitive_reference(command: str, *, depth: int = 0) -> bool:
    """词法区分命令路径、解释器源码和 heredoc 输入；不执行任何输入。

    仅 cat/tee 的正文按数据处理；解释器检查源码，未知消费者保守检查引用。
    无法完成词法分析或嵌套过深时回到保守检查，不授予新的执行能力。
    """
    if depth >= 12:
        return is_sensitive_reference(command)
    lines = command.splitlines(keepends=True)
    pending: deque[tuple[str, bool, str, bool]] = deque()
    index = 0
    while index < len(lines):
        if pending:
            delimiter, quoted, consumer, strip_tabs = pending.popleft()
            body = []
            while index < len(lines):
                line = lines[index].lstrip('\t') if strip_tabs else lines[index]
                if line.rstrip('\r\n') == delimiter:
                    break
                body.append(lines[index])
                index += 1
            if index == len(lines):
                return is_sensitive_reference(command)
            index += 1
            text = ''.join(body)
            if not quoted and _expansion_has_reference(text, depth):
                return True
            if consumer in _SHELLS:
                if shell_has_sensitive_reference(text, depth=depth + 1):
                    return True
            elif consumer in _INTERPRETERS:
                if code_has_sensitive_reference(text):
                    return True
            elif consumer not in _DATA_INPUTS and is_sensitive_reference(text):
                return True
            continue

        # 带引号的多行源码或反斜线续行必须作为同一条命令分析。
        header = ''
        while index < len(lines):
            header += lines[index]
            index += 1
            try:
                lexer = shlex.shlex(header, posix=False, punctuation_chars=';&|<>()')
                lexer.whitespace_split = True
                lexer.commenters = ''
                words = list(lexer)
            except ValueError:
                if index < len(lines):
                    continue
                return is_sensitive_reference(command)
            if header.rstrip('\r\n').endswith('\\') and index < len(lines):
                continue
            break

        # 管道下游可能解释上游数据，未知数据流不能套用正文/输出参数例外。
        if any('|' in word and not word.startswith(("'", '"')) for word in words):
            return is_sensitive_reference(command)

        consumer = ''
        code_next = False
        heredoc_next = False
        redirect_next = False
        for word in words:
            if word.startswith('#'):
                break
            if heredoc_next:
                strip_tabs = word.startswith('-')
                if strip_tabs:
                    word = word[1:]
                quoted = word.startswith(("'", '"', '\\'))
                delimiter = _unquote(word).removeprefix('\\')
                # 混合引号或动态分隔符不套用数据正文例外。
                if not delimiter or re.search(r'''[\s"'`$\\]''', delimiter):
                    return is_sensitive_reference(command)
                pending.append((delimiter, quoted, consumer, strip_tabs))
                heredoc_next = False
                continue
            if word in {'<<', '<<-'}:
                heredoc_next = True
                continue
            if word in _SEPARATORS:
                consumer, code_next, redirect_next = '', False, False
                continue
            if set(word) <= {'<', '>', '&'}:
                redirect_next = True
                continue
            if code_next:
                source = _unquote(word)
                if consumer in _SHELLS:
                    sensitive = shell_has_sensitive_reference(source, depth=depth + 1)
                else:
                    sensitive = code_has_sensitive_reference(source)
                if sensitive:
                    return True
                code_next = False
                continue
            if not word.startswith("'") and _expansion_has_reference(word, depth):
                return True
            if redirect_next or consumer not in _DATA_ARGUMENTS:
                if _path_word(word):
                    return True
            if redirect_next:
                redirect_next = False
                continue
            redirect_next = False
            if not consumer and not re.match(r'^[A-Za-z_][A-Za-z0-9_]*=', word):
                consumer = re.split(r'[\\/]', _unquote(word))[-1].lower().removesuffix('.exe')
            elif consumer in _SHELLS | _INTERPRETERS and word.lower() in {
                '-c', '-e', '-p', '--eval', '--print', '-command', '-commandwithargs',
            }:
                code_next = True
        if heredoc_next:
            return is_sensitive_reference(command)
    return bool(pending) and is_sensitive_reference(command)
