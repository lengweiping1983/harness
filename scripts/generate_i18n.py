"""从唯一资源源生成浏览器文案，避免将 Agent 长提示词送入前端包。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ('ui.', 'settings.language.', 'files.open.', 'browser.')


def main() -> int:
    """检查生成文件同步，或更新前端的只读资源视图。"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    failures = []
    startup = {}
    for language in ('zh-CN', 'en'):
        source = ROOT / 'harness' / 'i18n' / 'locales' / f'{language}.json'
        values = json.loads(source.read_text(encoding='utf-8'))
        startup[language] = {key: values[key] for key in ('ui.App.f76a8d866552', 'ui.startup.failed', 'ui.deferred.reload')}
        content = json.dumps({key: value for key, value in values.items() if key.startswith(PREFIXES)}, ensure_ascii=False, indent=2) + '\n'
        destination = ROOT / 'web' / 'src' / 'i18n' / 'locales' / f'{language}.json'
        if args.check:
            if not destination.exists() or destination.read_text(encoding='utf-8') != content:
                failures.append(str(destination.relative_to(ROOT)))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists() or destination.read_text(encoding='utf-8') != content:
                destination.write_text(content, encoding='utf-8')
    # 启动占位和失败提示不能依赖尚未下载的完整语言包。
    destination = ROOT / 'web' / 'src' / 'i18n' / 'startup.generated.json'
    content = json.dumps(startup, ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not destination.exists() or destination.read_text(encoding='utf-8') != content:
            failures.append(str(destination.relative_to(ROOT)))
    else:
        if not destination.exists() or destination.read_text(encoding='utf-8') != content:
            destination.write_text(content, encoding='utf-8')
    if failures:
        print('前端语言资源未同步，请运行 python3 scripts/generate_i18n.py：' + ', '.join(failures))
        return 1
    print('前端语言资源：同步')
    return 0


if __name__ == '__main__':
    from python_bootstrap import configure_utf8_process
    configure_utf8_process()
    raise SystemExit(main())
