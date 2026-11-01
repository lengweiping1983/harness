"""统一语言选择、系统地区检测与回合语言上下文。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from ..i18n import tr

LANGUAGES = ("auto", "zh-CN", "en")
CHINESE_REGIONS = frozenset({"CN", "HK", "MO", "TW"})
current_language: ContextVar[str] = ContextVar("harness_language", default="zh-CN")


def normalize_language(value: str) -> str:
    """只接受当前配置协议中的三个语言名称。"""
    if not isinstance(value, str) or value not in LANGUAGES:
        raise ValueError(tr("language.invalid_preference"))
    return value


def locale_region(value: str) -> str | None:
    """解析带编码、脚本或修饰符的系统 locale，不用语言推断地区。"""
    parts = re.split(r"[-_]", value.split(".")[0].split("@")[0])
    for part in parts[1:]:
        if re.fullmatch(r"[A-Za-z]{2}", part):
            return part.upper()
    return None


def detect_region() -> tuple[str | None, str]:
    """有界读取系统地区；不改变线程共享的全局 locale。"""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["/usr/bin/defaults", "read", "-g", "AppleLocale"],
                                    capture_output=True, text=True, timeout=1, check=False, encoding='utf-8')
            if result.returncode == 0:
                return locale_region(result.stdout.strip()), "AppleLocale"
        elif sys.platform == "win32":
            import ctypes
            buffer = ctypes.create_unicode_buffer(16)
            if ctypes.windll.kernel32.GetUserDefaultGeoName(buffer, len(buffer)):
                region = buffer.value.upper()
                return (region if re.fullmatch(r"[A-Z]{2}", region) else None), "GetUserDefaultGeoName"
        else:
            for key in ("LC_ALL", "LC_TIME", "LANG"):
                if os.environ.get(key):
                    return locale_region(os.environ[key]), key
    except (OSError, subprocess.TimeoutExpired, AttributeError, ValueError):
        pass
    return None, "unavailable"


@dataclass(frozen=True, slots=True)
class LanguageInfo:
    """可展示的语言选择和检测证据。"""
    language: str
    effective_language: str
    system_region: str | None
    detection_source: str

    def to_dict(self) -> dict:
        return {"language": self.language, "effective_language": self.effective_language,
                "system_region": self.system_region, "detection_source": self.detection_source}


def resolve_language(value: str = "auto") -> LanguageInfo:
    """地区只决定自动选择；手动选择始终优先。"""
    preference = normalize_language(value)
    region, source = detect_region()
    actual = ("zh-CN" if region in CHINESE_REGIONS else "en") if preference == "auto" else preference
    return LanguageInfo(preference, actual, region, source)


def saved_language(home: Path) -> str | None:
    """读取当前设置封装；格式错误明确报错且不改写原文件。"""
    try:
        raw = json.loads((home / "ui-settings.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(tr("language.invalid_settings_format")) from exc
    if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1 or not isinstance(raw.get("settings"), dict):
        raise ValueError(tr("language.invalid_settings_format"))
    inner = raw["settings"]
    return normalize_language(inner["language"]) if "language" in inner else None
