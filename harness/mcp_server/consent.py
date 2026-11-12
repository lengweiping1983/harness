"""独立 OAuth 授权页：本地字体、脚本零依赖、真实权限与可恢复表单。"""
from __future__ import annotations

import base64
import hashlib
import html
from ..i18n import tr
from ..core.language import current_language
from urllib.parse import urlsplit


STYLE = '''
*{box-sizing:border-box}body{margin:0;background:#f1f5f9;color:#20334c;font-family:"Segoe UI","Microsoft YaHei",sans-serif;-webkit-font-smoothing:antialiased}
.shell{max-width:1180px;margin:auto;padding:38px 40px 24px}.brand{display:flex;align-items:center;gap:12px;font-size:22px;font-weight:650;letter-spacing:-.6px}.mark{display:grid;place-items:center;width:37px;height:37px;background:#183e69;color:white;border-radius:10px;font-size:23px}.brand small{margin-left:auto;font-size:13px;font-weight:400;color:#61778f;letter-spacing:0}
main{display:grid;grid-template-columns:1.05fr 1fr;gap:68px;align-items:center;min-height:610px}.intro h2{font-size:clamp(36px,4.6vw,58px);letter-spacing:-2px;line-height:1.22;margin:0 0 24px;font-weight:650;color:#152d4d}.intro>p{font-size:16px;line-height:1.9;max-width:390px;color:#60748b}.bridge{margin:38px 0 30px;display:flex;align-items:center;gap:12px;max-width:390px}.node{flex:1;padding:18px 7px;text-align:center;background:#e4edf7;border-radius:9px;font-size:13px;color:#365b82}.node strong{display:block;font-size:18px;margin-bottom:6px;color:#183e69}.line{height:1px;width:26px;background:#83a4c5}.caption{font-size:12px!important;color:#738398!important}
.panel{background:white;border:1px solid #e0e7ef;border-radius:18px;padding:35px 36px;box-shadow:0 18px 65px rgba(24,62,105,.07)}.request{display:flex;align-items:center;gap:9px;font-size:13px;color:#526f8d}.dot{width:7px;height:7px;border-radius:50%;background:#298579}h1{font-size:29px;letter-spacing:-.9px;color:#183354;margin:14px 0 10px}.summary{font-size:14px;line-height:1.8;color:#697d92;margin:0 0 22px}.permissions{list-style:none;padding:0;margin:0 0 23px;display:grid;grid-template-columns:1fr 1fr;gap:12px 16px}.permissions li{display:flex;gap:9px;align-items:center;font-size:13px}.check{color:#347c94;font-size:15px}.scope{font-size:12px;color:#657c94;line-height:1.8;padding:12px 14px;background:#f4f7fb;border-radius:8px;margin-bottom:23px}
label{font-size:13px;font-weight:600;display:block;margin:0 0 9px}input[type=password]{width:100%;height:50px;border:1px solid #cbd8e6;border-radius:8px;padding:0 14px;font-size:17px;outline:none;background:#fcfdff;color:#20334c}input:focus{border-color:#2566ad;box-shadow:0 0 0 3px #dceafb}.hint{font-size:12px;line-height:1.7;color:#8190a1;margin:10px 0 19px}button{border:0;border-radius:8px;background:#205c9c;color:white;font:600 15px "Segoe UI","Microsoft YaHei",sans-serif;width:100%;height:50px;cursor:pointer;box-shadow:0 4px 12px #205c9c20}button:hover{background:#174b84}button:focus-visible,summary:focus-visible{outline:3px solid #83b8da;outline-offset:3px}.error{border-left:3px solid #bd514f;background:#fff4f3;color:#8c3937;font-size:13px;line-height:1.7;padding:10px 13px;margin-bottom:18px}.details{margin-top:20px;padding-top:18px;border-top:1px solid #edf1f6;font-size:12px;color:#8290a0}.details summary{cursor:pointer;color:#68809a}.details p{line-height:1.7;overflow-wrap:anywhere}.details code{font-size:11px}footer{border-top:1px solid #dbe4ee;padding-top:18px;font-size:12px;color:#8392a3;display:flex;justify-content:space-between;gap:14px}.standalone{max-width:510px;margin:80px auto}
@media(max-width:850px){.shell{padding:24px}main{gap:32px;grid-template-columns:1fr 1fr}.panel{padding:28px 24px}.intro h2{font-size:40px}.bridge{gap:7px}.line{width:12px}}
@media(max-width:650px){.shell{padding:22px 18px}.brand small{display:none}main{display:block;min-height:0;padding:35px 0}.intro h2{font-size:34px;letter-spacing:-1px;margin-bottom:12px}.intro>p{font-size:14px;margin:0 0 25px}.bridge,.caption{display:none}.panel{padding:27px 23px}h1{font-size:26px}footer{font-size:11px}.standalone{margin:35px auto}}
'''


def headers():
    """只允许固定样式摘要；不允许脚本、外部字体、嵌入或缓存。"""
    digest = base64.b64encode(hashlib.sha256(STYLE.encode()).digest()).decode()
    return {'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin',
        'Content-Security-Policy': "default-src 'none'; style-src 'sha256-" + digest + "'; base-uri 'none'; frame-ancestors 'none'"}


def page(ticket='', nonce='', params=None, *, error='', all_projects=False):
    """资源和请求内容均编码后展示，保持表单协议及原始错误。"""
    esc = html.escape
    def copy(key, **values):
        return esc(tr('execution.consent.' + key, **values), quote=True)
    params = params or {}
    destination = str(params.get('redirect_uri', ''))
    host = urlsplit(destination).hostname
    client = 'ChatGPT' if host in {'chatgpt.com', 'chat.openai.com'} else host or tr('execution.consent.client')
    labels = {'project.read': 'read', 'project.write': 'write', 'command.run': 'run', 'git.commit': 'commit'}
    scopes = params.get('scopes', [])
    permissions = ''.join('<li><span class="check" aria-hidden="true">✓</span>' + (copy(labels[scope]) if scope in labels else esc(scope)) + '</li>' for scope in scopes)
    message = '<p class="error" role="alert">' + esc(error) + '</p>' if error else ''
    if nonce:
        form = f'''<form method="post" action="/consent?ticket={esc(ticket, quote=True)}"><input type="hidden" name="csrf" value="{esc(nonce, quote=True)}"><label for="password">{copy('password')}</label><input id="password" type="password" name="password" required autocomplete="current-password" maxlength="256" placeholder="{copy('placeholder')}"><p class="hint">{copy('hint')}</p><button type="submit">{copy('submit')}</button></form>'''
        body = f'''<div class="request"><span class="dot" aria-hidden="true"></span>{copy('request', client=client)}</div><h1>{copy('title')}</h1><p class="summary">{copy('summary')}</p><ul class="permissions">{permissions}</ul><p class="scope">{copy('all' if all_projects else 'selected')}</p>{message}{form}<details class="details"><summary>{copy('details')}</summary><p>{copy('callback')}<br><code>{esc(destination)}</code></p><p>{copy('permissions', scopes=' '.join(scopes))}</p></details>'''
    else:
        body = f'<h1>{copy("unavailable")}</h1>{message}<p class="summary">{copy("retry")}</p>'
    return f'''<!doctype html><html lang="{esc(current_language.get(), quote=True)}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{copy('title')}</title><style>{STYLE}</style></head><body><div class="shell"><header class="brand"><span class="mark" aria-hidden="true">H</span>Harness<small>{copy('tagline')}</small></header><main><section class="intro"><h2>{copy('heading')}</h2><p>{copy('intro')}</p><div class="bridge" aria-label="{copy('bridge')}"><div class="node"><strong>{copy('model')}</strong>{copy('reasoning')}</div><span class="line"></span><div class="node"><strong>Harness</strong>{copy('execution')}</div><span class="line"></span><div class="node"><strong>{copy('project')}</strong>{copy('verify')}</div></div><p class="caption">{copy('caption')}</p></section><section class="panel" aria-label="{copy('panel')}">{body}</section></main><footer><span>{copy('footer')}</span><span>OAuth 2.0 · PKCE</span></footer></div></body></html>'''
