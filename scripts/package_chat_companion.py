"""生成可直接加载的浏览器扩展包及来自双语资源的离线安装说明。"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import tempfile
import zipfile


ASSETS = ('manifest.json', 'background.mjs', 'client.mjs', 'content.js', 'page.mjs', 'secret-input.mjs',
          'popup.html', 'popup.css', 'popup.mjs', '_locales/en/messages.json', '_locales/zh_CN/messages.json')
GUIDE = ('install_unpack', 'install_chrome', 'install_pin', 'install_backend', 'install_command',
         'setup_bridge', 'setup_pair', 'setup_chat', 'setup_remote', 'install_safety')


def package(source: Path, target: Path):
    """只收集明确的产品资源，根目录放置清单，不打包运行数据或配对码。"""
    files = {name: (source / name).read_bytes() for name in ASSETS}
    sections, readme = [], []
    for language in ('zh_CN', 'en'):
        messages = json.loads(files[f'_locales/{language}/messages.json'])
        title = messages['install_title']['message']
        parts = [messages[key]['message'] for key in GUIDE]
        items = ''.join('<li>' + html.escape(part) + '</li>' for part in parts)
        lang = 'zh-CN' if language == 'zh_CN' else 'en'
        sections.append(f'<section lang="{lang}"><h1>{html.escape(title)}</h1><ol>{items}</ol></section>')
        readme.append(title + '\n' + messages['install_readme']['message'] + '\n\n' + '\n'.join(parts))
    document = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>Harness Chat Companion</title><style>'
                'body{max-width:800px;margin:32px auto;padding:0 20px;font:16px/1.7 system-ui,sans-serif;'
                'color-scheme:light dark}h1{font-size:28px}li{margin:12px 0;overflow-wrap:anywhere}'
                'section+section{border-top:1px solid #8887;margin-top:32px;padding-top:16px}'
                '</style>' + ''.join(sections) + '</html>')
    files['INSTALL.html'] = document.encode('utf-8')
    files['README.txt'] = '\n\n'.join(readme).encode('utf-8')
    manifest = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    files['SHA256.json'] = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix='companion-package-', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in sorted(files.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, content)
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise ValueError('Invalid companion package')
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'path': str(target), 'files': len(files), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def main():
    """开发打包入口；不会安装浏览器扩展或启动任何服务。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parent.parent / 'extensions' / 'chatgpt-companion'
    print(json.dumps(package(source, args.output), ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
