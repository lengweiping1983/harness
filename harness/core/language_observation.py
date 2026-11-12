"""有界观测返回思考的叙述语言，不改写原文或供应商续传数据。"""
from __future__ import annotations

import re

SAMPLE_CHARS = 65536
FUNCTION_WORDS = frozenset('i we the a an is are be will would should need to and or for from with this that it let me can not then so have has of in on do does but first before after my our if'.split())


def observe_thinking_language(text: str, expected_language: str, *, proper_names=()) -> dict:
    """仅标记疑似偏离；代码、引文和标识符不作为自然语言依据。"""
    sample = text[:SAMPLE_CHARS]
    sample = re.sub(r'```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)', ' ', sample)
    sample = re.sub(r'"[^"\n]*"|“[^”\n]*”|「[^」\n]*」|(?<!\w)\x27[^\x27\n]*\x27(?!\w)', ' ', sample)
    sample = re.sub(r'https?://\S+|(?:[A-Za-z]:)?[/\\][\w./\\-]+|\b[\w-]+\.(?:py|ts|tsx|js|jsx|json|md|txt|html|css)\b', ' ', sample)
    sample = re.sub(r'\b[A-Za-z_$][\w.$]*\([^()\n]*\)', ' ', sample)
    names = [re.escape(str(name)) for name in proper_names if name]
    if names:
        sample = re.sub(r'(?<!\w)(?:' + '|'.join(names) + r')(?!\w)', ' ', sample)
    # 无围栏的常见代码行也不用于判断叙述语言。
    sample = '\n'.join(' ' if re.search(r'[{};=]|^\s*(?:def|class|function|const|import|return)\b', line)
                       else line for line in sample.splitlines())
    sample = re.sub(r'\b[A-Za-z_][A-Za-z0-9_]*\b',
                    lambda match: ' ' if ('_' in match[0] or any(c.isdigit() for c in match[0])
                    or match[0].isupper() and len(match[0]) > 1
                    or any(c.isupper() for c in match[0][1:])) else match[0], sample)
    han_chars = sum('\u4e00' <= char <= '\u9fff' for char in sample)
    spans = re.findall(r"[A-Za-z][A-Za-z \t',.:!?()\-]*", sample)
    english_spans = 0
    english_words = 0
    for span in spans:
        words = re.findall(r"[A-Za-z]+(?:'[A-Za-z]+)?", span.lower())
        if len(words) >= 8 and sum(word in FUNCTION_WORDS for word in words) >= 3:
            english_spans += 1
            english_words += len(words)
    suspected = english_spans > 0 if expected_language == 'zh-CN' else han_chars >= 16
    return {'version': 1, 'channel': 'thinking', 'expected_language': expected_language,
            'status': 'suspected_drift' if suspected else 'no_drift_detected' if han_chars or english_spans else 'insufficient_prose',
            'sampled_chars': min(len(text), SAMPLE_CHARS), 'truncated': len(text) > SAMPLE_CHARS,
            'han_prose_chars': han_chars, 'english_prose_spans': english_spans,
            'english_prose_words': english_words}
