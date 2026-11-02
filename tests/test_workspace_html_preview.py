"""网页预览：HTML 内联仍然关进沙箱，站点相对资源按目录结构解析。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.core.language import current_language
from harness.web.api.workspace import build_router
from harness.web.errors import ApiError
from harness.web.router import Request


def _ctx(root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=lambda: root,
        workspaces=SimpleNamespace(get=lambda _key: None),
        shared_access=None,
    )


def _request(query: dict[str, list[str]]) -> Request:
    return Request(
        method='GET', path='/api/v1/file', query=query, headers={}, body=b'',
        params={}, client_ip='127.0.0.1',
    )


class WorkspaceHtmlPreviewTests(unittest.TestCase):
    def test_html_inline_is_sandboxed_and_download_stays_attachment(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                (root / 'index.html').write_text('<h1>原文</h1>', encoding='utf-8')
                router = build_router(_ctx(root))
                handler, _ = router.resolve('GET', '/api/v1/file')

                inline = handler(_request({'path': ['index.html']}))
                self.assertEqual(inline.status, 200)
                self.assertIn('inline', inline.headers['Content-Disposition'])
                self.assertEqual(inline.headers['Content-Security-Policy'], 'sandbox allow-scripts')
                self.assertEqual(inline.headers['X-Content-Type-Options'], 'nosniff')

                download = handler(_request({'path': ['index.html'], 'download': ['1']}))
                self.assertIn('attachment', download.headers['Content-Disposition'])
                self.assertNotIn('Content-Security-Policy', download.headers)

                # 图片等既有媒体类型不受沙箱策略影响。
                (root / 'shot.png').write_bytes(b'\x89PNG\r\n\x1a\n')
                image = handler(_request({'path': ['shot.png']}))
                self.assertNotIn('Content-Security-Policy', image.headers)

    def test_site_preview_resolves_relative_assets_and_rejects_escape(self):
        for language in ('en', 'zh-CN'):
            with current_language.set(language), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                site = root / 'site'
                site.mkdir()
                (site / 'index.html').write_text('<link rel="stylesheet" href="styles.css">', encoding='utf-8')
                (site / 'styles.css').write_text('body { color: red }', encoding='utf-8')
                (root / 'secret.txt').write_text('原文', encoding='utf-8')
                router = build_router(_ctx(root))
                page, _ = router.resolve('GET', '/api/v1/preview/site/index.html')
                root_route, params = router.resolve('GET', '/api/v1/preview/index.html')
                self.assertEqual(params, {'entry': 'index.html'})

                response = page(_request({}), entry='index.html', folder='site')
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Type'], 'text/html')
                self.assertEqual(response.headers['Content-Security-Policy'], 'sandbox allow-scripts')
                self.assertEqual(response.body.decode('utf-8'), '<link rel="stylesheet" href="styles.css">')

                # 同一条路由按目录解析出样式表：页面里的相对路径指向工作区里的真实文件。
                style = page(_request({}), entry='styles.css', folder='site')
                self.assertEqual(style.status, 200)
                self.assertEqual(style.headers['Content-Type'], 'text/css')
                self.assertNotIn('Content-Security-Policy', style.headers)

                # 工作区根下的页面走两片段路由，folder 取默认值即根。
                with self.assertRaises(ApiError) as missing_root:
                    root_route(_request({}), **params)
                self.assertEqual(missing_root.exception.status, 404)
                (root / 'index.html').write_text('<h1>根页面</h1>', encoding='utf-8')
                self.assertEqual(root_route(_request({}), **params).status, 200)

                with self.assertRaises(ApiError) as missing:
                    page(_request({}), entry='missing.html', folder='site')
                self.assertEqual(missing.exception.status, 404)
                # 站点目录内向上引用工作区其它文件是允许的，跳出工作区根才拒。
                self.assertEqual(page(_request({}), entry='../secret.txt', folder='site').status, 200)
                with self.assertRaises(ApiError) as failure:
                    page(_request({}), entry='../../secret.txt', folder='site')
                self.assertEqual(failure.exception.status, 403)
