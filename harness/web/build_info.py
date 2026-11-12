"""未构建工作台时展示启动说明，不提供另一套聊天实现。"""
from __future__ import annotations

from html import escape
from harness.i18n import resources


def render_build_info(language: str) -> str:
    """使用当前主题和双语资源生成不依赖构建产物的说明页。"""
    strings = resources(language)
    title = escape(strings['web.build.title'])
    description = escape(strings['web.build.description'])
    return f'''<!doctype html>
<html lang="{escape(language)}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Harness</title>
<style>
:root{{color-scheme:light;--canvas:#f6f7f8;--surface:#fff;--ink:#20292c;--muted:#55656b;--line:#dde2e4;--brand:#16736b}}
.dark{{color-scheme:dark;--canvas:#181c1e;--surface:#222729;--ink:#e9eeef;--muted:#b4c0c4;--line:#394144;--brand:#6ecebb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--canvas);color:var(--ink);font:0.875rem/1.5 'Segoe UI','Microsoft YaHei UI',-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}}
main{{max-width:44rem;margin:12vh auto;padding:2rem}}article{{padding:2rem;border:1px solid var(--line);border-radius:1rem;background:var(--surface)}}
h1{{margin:0;font-size:1.25rem;line-height:1.75rem;font-weight:600}}p{{color:var(--muted);margin:1rem 0}}pre{{overflow:auto;padding:1rem;border-radius:.5rem;background:var(--canvas);font:0.8125rem/1.25rem ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}header{{margin-bottom:1rem;color:var(--brand);font-weight:600}}
</style><script>try{{var t=localStorage.getItem('harness.ui-theme');document.documentElement.classList.toggle('dark',t==='dark'||t!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches)}}catch(e){{}}</script>
</head><body><main><header>Harness</header><article><h1>{title}</h1><p>{description}</p>
<pre>npm --prefix web ci
npm --prefix web run build</pre><p>{escape(strings['web.build.restart'])}</p><pre>harness --ui</pre>
</article></main></body></html>'''
