"""由唯一矢量原稿生成 Web 与桌面品牌资源；使用 Pillow 进行抗锯齿栅格化。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'assets/brand/harness.svg'
MANIFEST = ROOT / 'assets/brand/manifest.json'


def digest(file):
    """记录源与分发文件的真实字节身份。"""
    return hashlib.sha256(file.read_bytes()).hexdigest()


def generate():
    """从 SVG 的矩形、直线轮廓与配色生成各平台格式。"""
    from PIL import Image, ImageDraw
    import re

    svg = ET.fromstring(SOURCE.read_text(encoding='utf-8'))
    image = Image.new('RGBA', (2048, 2048))
    draw = ImageDraw.Draw(image)
    scale = 16
    for element in svg:
        if element.tag.endswith('rect'):
            draw.rounded_rectangle((0, 0, 2047, 2047), radius=int(element.attrib['rx']) * scale, fill=element.attrib['fill'])
        else:
            commands = re.findall(r'([MLHVZ])([^MLHVZ]*)', element.attrib['d'])
            points = []
            x = y = 0
            for command, values in commands:
                numbers = [float(value) for value in values.split()]
                if command in ('M', 'L'):
                    x, y = numbers
                elif command == 'H':
                    x = numbers[0]
                elif command == 'V':
                    y = numbers[0]
                else:
                    continue
                points.append((x * scale, y * scale))
            draw.polygon(points, fill=element.attrib['fill'])
    public = ROOT / 'web/public/brand'
    desktop = ROOT / 'desktop/assets'
    public.mkdir(parents=True, exist_ok=True)
    desktop.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE, public / 'harness.svg')
    png = image.resize((1024, 1024), Image.Resampling.LANCZOS)
    png.save(desktop / 'harness.png')
    png.save(desktop / 'harness.ico', sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
    # macOS 位图图标在 1024 画布中居中放置 824 图案，四周各留 100 透明边距。
    macos = Image.new('RGBA', image.size)
    macos.alpha_composite(image.resize((1648, 1648), Image.Resampling.LANCZOS), (200, 200))
    macos.resize((1024, 1024), Image.Resampling.LANCZOS).save(desktop / 'harness-macos.png')
    chunks = []
    for size, kind in ((128, b'ic07'), (256, b'ic08'), (512, b'ic09'), (1024, b'ic10')):
        buffer = io.BytesIO()
        macos.resize((size, size), Image.Resampling.LANCZOS).save(buffer, format='PNG')
        data = buffer.getvalue()
        chunks.append(kind + struct.pack('>I', len(data) + 8) + data)
    payload = b''.join(chunks)
    (desktop / 'harness.icns').write_bytes(b'icns' + struct.pack('>I', len(payload) + 8) + payload)
    for size in (180, 192, 512):
        image.resize((size, size), Image.Resampling.LANCZOS).save(public / f'harness-{size}.png')
    files = [SOURCE, *sorted(public.iterdir()), desktop / 'harness-macos.png', *sorted(desktop.glob('harness.*'))]
    MANIFEST.write_text(json.dumps({file.relative_to(ROOT).as_posix(): digest(file) for file in files}, indent=2) + '\n', encoding='utf-8')


def main():
    """同步检查只依赖标准库，普通构建无需安装绘图库。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if args.check:
        for name, expected in json.loads(MANIFEST.read_text(encoding='utf-8')).items():
            if digest(ROOT / name) != expected:
                raise SystemExit(f'Brand asset out of sync: {name}')
        print('Brand assets verified.')
    else:
        generate()


if __name__ == '__main__':
    main()
