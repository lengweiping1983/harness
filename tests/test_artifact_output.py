"""项目产物路径、模型反馈和交付声明使用真实调用目录并保留外部原件。"""
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

from harness import Harness, HarnessConfig
from harness.core.events import EventBus
from harness.core.language import current_language
from harness.i18n import resources, tr
from harness.llm.types import ToolUseBlock
from harness.tools.builtin.fs_tools import build_fs_tools
from harness.tools.builtin.present_tools import build_present_tools
from harness.tools.registry import ToolRegistry, ToolRunContext
from tests.agent_configuration_fixture import RecordingTransport
from tests.filesystem_links import directory_link


class ArtifactOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project-{language}-原文'
        self.project.mkdir()
        self.other = self.root / 'other'
        self.other.mkdir()
        self.bus = EventBus()
        self.events = []
        self.bus.on('deliverables/presented', self.events.append)
        self.registry = ToolRegistry()
        self.tools = {tool.schema.name: tool for tool in
                      build_fs_tools(str(self.other)) + build_present_tools(self.bus, cwd=str(self.other))}
        for tool in self.tools.values():
            self.registry.register(tool)

    async def call(self, name, args, *, cwd=None, language='en'):
        context = ToolRunContext('artifact', 'owner', str(cwd or self.project), 1, 1)
        with current_language.set(language):
            return await self.registry.execute_call(ToolUseBlock(id='artifact', name=name, input=args), context)

    async def test_relative_svg_write_and_present_use_conversation_project(self):
        source = '<svg xmlns="http://www.w3.org/2000/svg"><text>原文 {name}</text></svg>\n'
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            path = f'images/animation-{index}.svg'
            with current_language.set(language):
                for name, key in (('write', 'python.fs_tools.56485391272f'),
                                  ('present', 'python.present_tools.2cfa899caa53')):
                    values = {'p0': 2, 'p1': 8} if name == 'present' else {}
                    self.assertEqual(self.tools[name].schema.resolve_description(language).description, tr(key, **values))
            written = await self.call('write', {'path': path, 'content': source}, language=language)
            self.assertFalse(written.is_error, written.content)
            actual = self.project / path
            self.assertEqual(actual.read_text(encoding='utf-8'), source)
            self.assertEqual(written.display['path'], str(actual))
            self.assertNotIn('_workspace_root', written.display)
            with current_language.set(language):
                self.assertEqual(written.content[0].text, tr('fs.write.summary.created', path=str(actual), size=actual.stat().st_size))
            shown = await self.call('present', {'files': [{'path': path, 'description': 'Original 描述'}]}, language=language)
            self.assertFalse(shown.is_error, shown.content)
            self.assertEqual(shown.data['workspace_root'], str(self.project))
            self.assertEqual(shown.data['files'][0]['path'], str(actual))
            self.assertEqual(shown.data['files'][0]['description'], 'Original 描述')
        self.assertEqual(len(self.events), 3)
        self.assertFalse((self.other / 'images').exists())

    async def test_outside_write_receipt_reports_success_and_project_location(self):
        for index, language in enumerate(('en', 'zh-CN', 'en')):
            target = self.other / f'temporary-{index}.svg'
            result = await self.call('write', {'path': str(target), 'content': '<svg/>'}, language=language)
            self.assertFalse(result.is_error, result.content)
            self.assertEqual(target.read_text(encoding='utf-8'), '<svg/>')
            with current_language.set(language):
                self.assertIn(tr('fs.write.outside_workspace', path=str(target), root=str(self.project)), result.content[0].text)
            self.assertEqual(list(self.project.iterdir()), [])

    async def test_present_rejects_external_traversal_links_and_mixed_batch_without_copying(self):
        outside = self.other / 'outside.svg'
        outside.write_text('<svg>原件</svg>', encoding='utf-8')
        (self.project / 'valid.svg').write_text('<svg/>', encoding='utf-8')
        directory_link(self.project / 'linked', self.other)
        for language in ('en', 'zh-CN', 'en'):
            for path in (str(outside), '../other/outside.svg', 'linked/outside.svg'):
                with self.subTest(language=language, path=path):
                    result = await self.call('present', {'files': [{'path': 'valid.svg'}, {'path': path}]}, language=language)
                    self.assertTrue(result.is_error)
                    self.assertIn('[PRESENT_OUTSIDE_WORKSPACE]', result.display['error'])
                    with current_language.set(language):
                        self.assertIn(tr('present.outside_workspace', path=str(outside), root=str(self.project)), result.content[0].text)
                    self.assertEqual(self.events, [])
                    self.assertEqual(outside.read_text(encoding='utf-8'), '<svg>原件</svg>')
                    self.assertFalse((self.project / 'outside.svg').exists())
        own = await self.call('present', {'files': [{'path': str(outside)}]}, cwd=self.other)
        self.assertFalse(own.is_error, own.content)
        self.assertEqual(own.data['workspace_root'], str(self.other))

    async def test_actual_model_requests_use_current_project_and_bilingual_output_policy(self):
        flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
        flags['enable_fs'] = True
        transport = RecordingTransport()
        host = Harness(HarnessConfig(home=self.root / 'state', cwd=self.other, api_key='fixture', model='fixture',
            max_steps=2, max_retries=0, **flags), transport=transport)
        try:
            session = host.create_session(cwd=self.project)
            for language in ('en', 'zh-CN', 'en'):
                host.config.language = language
                await host.run_async(session, 'Original 用户请求')
                def texts(value):
                    """从真实请求协议中取正文，不依赖供应商的字段布局。"""
                    if isinstance(value, str):
                        yield value
                    elif isinstance(value, dict):
                        for item in value.values():
                            yield from texts(item)
                    elif isinstance(value, list):
                        for item in value:
                            yield from texts(item)
                request_texts = list(texts(transport.requests[-1]))
                self.assertTrue(any(resources(language)['prompt.artifact_output'] in text for text in request_texts))
                self.assertTrue(any(str(self.project) in text for text in request_texts))
                self.assertTrue(any(resources(language)['python.fs_tools.56485391272f'] == text for text in request_texts))
        finally:
            await host.close()
