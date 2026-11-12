"""Session route rejects out-of-range cursors and unknown model ids before work."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from harness.app.harness import Harness
from harness.session.store import SessionStore
from harness.web.api.sessions import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request


class SessionApiInputBoundsTests(unittest.TestCase):
    def test_context_rejects_negative_cursor(self):
        ctx = SimpleNamespace(harness=SimpleNamespace(), call=asyncio.run)
        endpoint, params = build_router(ctx).resolve('GET', '/api/v1/sessions/s/context')
        request = Request('GET', '/api/v1/sessions/s/context', {'through': ['-1']}, {}, b'')
        with patch('harness.web.context_audit.resolve_context', new=AsyncMock(return_value=object())):
            with self.assertRaises(BadRequest) as caught:
                endpoint(request, **params)
        self.assertEqual(caught.exception.code, 'search/invalid-position')

    def test_context_caps_page_size(self):
        ctx = SimpleNamespace(harness=SimpleNamespace(), call=asyncio.run)
        endpoint, params = build_router(ctx).resolve('GET', '/api/v1/sessions/s/context')
        session = SimpleNamespace(events=(), surface=())
        request = Request('GET', '/api/v1/sessions/s/context',
                          {'through': ['0'], 'limit': ['20001']}, {}, b'')
        with patch('harness.web.context_audit.resolve_context', new=AsyncMock(return_value=session)):
            with self.assertRaises(BadRequest) as caught:
                endpoint(request, **params)
        self.assertEqual(caught.exception.code, 'search/invalid-position')

    def test_create_rejects_model_outside_catalog_before_persisting(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = SessionStore(root)
            host = SimpleNamespace(sessions=store, config=SimpleNamespace(cwd=root), get_session=store.get)
            host.create_session = lambda **kwargs: Harness.create_session(host, **kwargs)
            endpoint, params = build_router(SimpleNamespace(harness=host)).resolve('POST', '/api/v1/sessions')
            request = Request('POST', '/api/v1/sessions', {},
                              {'X-Request-ID': 'invalid-model'},
                              json.dumps({'model': 'not-in-catalog'}).encode())
            with patch.object(store, 'create') as create:
                with self.assertRaises(BadRequest) as caught:
                    endpoint(request, **params)
            self.assertEqual(caught.exception.code, 'model/unknown')
            create.assert_not_called()
            self.assertEqual(store.list_sessions(), [])
