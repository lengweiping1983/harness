"""导入图标数据，不执行应用代码。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('asar', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
with args.asar.open('rb') as stream:
    header = struct.unpack('<4I', stream.read(16))
    inventory = json.loads(stream.read(header[3]))
    assets = inventory['files']['webview']['files']['assets']['files']
    def read(prefix):
        matches = [name for name in assets if name.startswith(prefix) and name.endswith('.js')]
        if len(matches) != 1:
            raise ValueError(f'资源不唯一：{prefix} {matches}')
        entry = assets[matches[0]]
        stream.seek(8 + header[1] + int(entry['offset']))
        return stream.read(entry['size']).decode()
    initial = read('app-initial-')
    shared = read('app-shared-')
    diff = read('code-diff-')

def table(source, name):
    match = re.search(r'\b' + name + r'=\{([^}]*)\}', source)
    if match is None:
        raise ValueError(f'找不到静态映射：{name}')
    pairs = re.findall(r'(?:"([^"]+)"|([\w]+)):`([^`]+)`', match[1])
    return {quoted or bare: value for quoted, bare, value in pairs}

attrs = {'class': 'className', 'fill-rule': 'fillRule', 'clip-rule': 'clipRule', 'stroke-width': 'strokeWidth', 'stroke-linecap': 'strokeLinecap', 'stroke-linejoin': 'strokeLinejoin', 'clip-path': 'clipPath', 'stop-color': 'stopColor', 'stop-opacity': 'stopOpacity'}
def jsx(element):
    if element.tag not in {'path', 'circle', 'rect', 'g', 'ellipse', 'polygon', 'polyline', 'line', 'defs', 'clipPath', 'linearGradient', 'stop'}:
        raise ValueError(f'不支持的图形：{element.tag}')
    if any(k.startswith('on') or k in {'href', 'src', 'style'} for k in element.attrib):
        raise ValueError('图形包含非静态属性')
    props = ' '.join(f'{attrs.get(k, k)}={json.dumps(v)}' for k, v in element.attrib.items())
    return f'<{element.tag} {props}>' + ''.join(jsx(child) for child in element) + f'</{element.tag}>'

bodies = {}
for token, body in re.findall(r'<symbol id="file-tree-builtin-([^"]+)" viewBox="0 0 16 16">(.*?)</symbol>', initial, re.S):
    body = re.sub(r'id="([^"]+)"', lambda m: f'id="harness-file-{token}-{m[1]}"', body)
    body = re.sub(r'url\(#([^)]+)\)', lambda m: f'url(#harness-file-{token}-{m[1]})', body)
    bodies[token] = ''.join(jsx(child) for child in ET.fromstring('<svg>' + body + '</svg>'))
if len(bodies) < 50:
    raise ValueError('文件图标集不完整')
# 只接受静态 SVG 图形，避免将应用代码或动态 HTML 带入仓库。
for token, name in {'command': 'terminal-light-16', 'search': 'magnifying-glass-md-light-16', 'edit': 'pencil-light-16', 'list': 'folder-on-folder-light-16', 'web': 'globe-light-16', 'computer': 'desktop-light-16', 'image': 'photo-on-square-light-16', 'agent': 'person-2-light-16', 'plan': 'list-checkmark-circle-light-16', 'skill': 'book-open-light-16', 'tool': 'wrench-light-16'}.items():
    match = re.search(r'name:`' + name + r'`.*?body:`([^`]+)`', shared)
    if match:
        bodies['tool-' + token] = ''.join(jsx(child) for child in ET.fromstring('<svg>' + match[1] + '</svg>'))

output = root / 'web/src/components/ui/codexIconData.tsx'
output.write_text('// 由 scripts/icons/import_codex_icons.py 生成，请勿手工编辑。\nimport type { ReactNode } from \'react\'\n\nexport const iconBodies: Record<string, ReactNode> = {\n' + ''.join(f'  {json.dumps(k)}: <>{v}</>,\n' for k, v in bodies.items()) + '}\n', encoding='utf-8')
data = {'byName': table(initial, 'xns'), 'byExtension': table(initial, 'Sns'), 'byCompoundExtension': table(initial, 'Cns'), 'colors': table(diff[diff.rfind('Jn='):], 'Jn'), 'tokens': table(diff[diff.rfind('Yn='):], 'Yn')}
(root / 'web/src/lib/codexFileIcons.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'file_icons': len([k for k in bodies if not k.startswith('tool-')]), 'tool_icons': [k for k in bodies if k.startswith('tool-')], 'asar_sha256': hashlib.sha256(args.asar.read_bytes()).hexdigest()}))
