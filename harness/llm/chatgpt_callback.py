"""独立登录回调页，不加载外部资源，也不保留授权查询参数。"""
from __future__ import annotations

from html import escape

from ..core.language import current_language
from ..i18n import tr


def render_callback(message: str, *, success: bool, nonce: str) -> str:
    """所有动态内容转义；页面只展示宿主提供的安全状态。"""
    language = escape(current_language.get(), quote=True)
    title = escape(tr('chatgpt.page.success' if success else 'chatgpt.page.failure'))
    description = escape(message)
    guidance = escape(tr('chatgpt.page.done' if success else 'chatgpt.page.retry'))
    footnote = escape(tr('chatgpt.page.private'))
    nonce = escape(nonce, quote=True)
    symbol = '<path d="m17 28 7 7 15-16"/>' if success else '<path d="M28 16v15m0 7v1"/>'
    state = 'success' if success else 'attention'
    return f'''<!doctype html>
<html lang="{language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>{title} · Harness</title>
<style nonce="{nonce}">
:root {{font-family:"Segoe UI","Microsoft YaHei",sans-serif;color-scheme:light dark;
--bg:#edf1f7;--panel:#fff;--ink:#152844;--muted:#52647b;--line:#d7dfeb;--accent:#325a98;--halo:#e4ecf9}}
* {{box-sizing:border-box}} body {{margin:0;min-height:100svh;background:var(--bg);color:var(--ink);
display:flex;flex-direction:column;padding:32px clamp(24px,6vw,96px)}}
header {{display:flex;align-items:center;gap:12px;font-size:16px;font-weight:600;letter-spacing:.02em}}
.mark {{width:32px;height:32px;display:grid;place-items:center;border:1px solid var(--line);border-radius:10px}}
.mark svg {{width:20px;height:20px}} main {{flex:1;display:grid;place-items:center;padding:48px 0}}
article {{width:min(100%,760px);text-align:center;background:var(--panel);border:1px solid var(--line);
border-radius:28px;padding:64px clamp(24px,6vw,72px);box-shadow:0 24px 80px #162c4d0a}}
.seal {{width:112px;height:112px;margin:0 auto 32px;border-radius:50%;display:grid;place-items:center;
background:var(--halo);border:1px solid var(--line);outline:1px solid var(--line);outline-offset:12px}}
.seal svg {{width:56px;height:56px;fill:none;stroke:var(--accent);stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}}
h1 {{font-size:28px;line-height:1.4;letter-spacing:-.03em;margin:0 0 20px;font-weight:600}}
.description {{font-size:16px;line-height:1.8;margin:0 auto;max-width:52ch;overflow-wrap:anywhere}}
.guidance {{font-size:14px;line-height:1.8;color:var(--muted);margin:32px 0 0;padding-top:24px;border-top:1px solid var(--line)}}
footer {{text-align:center;color:var(--muted);font-size:12px;line-height:1.8}}
@media(prefers-color-scheme:dark) {{:root {{--bg:#111b2c;--panel:#18263c;--ink:#eff4fc;--muted:#a9bad0;
--line:#30425d;--accent:#a8c8ff;--halo:#223753}}}}
@media(max-width:480px) {{body {{padding:24px 20px}} main {{padding:40px 0}} article {{padding:48px 24px;border-radius:24px}}}}
</style><script nonce="{nonce}">history.replaceState(null, '', '/auth/callback');</script></head>
<body><header><span class="mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 5v14M18 5v14M6 12h12"/></svg></span>Harness</header>
<main><article data-state="{state}" aria-labelledby="title"><div class="seal" aria-hidden="true"><svg viewBox="0 0 56 56">{symbol}</svg></div>
<h1 id="title">{title}</h1><p class="description" role="status">{description}</p><p class="guidance">{guidance}</p></article></main>
<footer>{footnote}</footer></body></html>'''
