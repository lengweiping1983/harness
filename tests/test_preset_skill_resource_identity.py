"""预设只按显式资源身份解析，技能文件与外部字段保持原文。"""
import copy
import tempfile
from dataclasses import fields
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.connectors.presets import ConnectorPreset, PRESETS, get_preset
from harness.connectors.store import ConnectorRecord
from harness.core.language import current_language
from harness.i18n import resources, tr
from harness.skills.parser import SkillMetadata
from harness.skills.paths import SYSTEM_SKILL_ROOT
from harness.skills.rendering import render_loaded_skill
from harness.skills.tool import build_skill_tool
from harness.web.views import skill_catalog


class PresetResourceIdentityTests(unittest.TestCase):
    def test_all_shared_presets_and_held_views_use_explicit_runtime_keys(self):
        originals = {name: copy.deepcopy(preset.record.to_dict()) for name, preset in PRESETS.items()}
        held = get_preset('github')
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                for name, registered in PRESETS.items():
                    preset = get_preset(name)
                    expected = tr(registered.description_key)
                    self.assertEqual(preset.record.metadata['description'], expected)
                    self.assertEqual(registered.summary()['description'], expected)
                    self.assertEqual(preset.summary()['description'], expected)
                    self.assertEqual(preset.summary()['auth_hint'], tr(registered.auth_hint_key))
                    if registered.skill_key:
                        self.assertEqual(preset.skill_markdown, tr(registered.skill_key))
                        self.assertTrue(preset.summary()['has_skill'])
                self.assertEqual(held.summary()['description'], tr(PRESETS['github'].description_key))
        self.assertEqual({name: preset.record.to_dict() for name, preset in PRESETS.items()}, originals)

    def test_resolved_install_records_do_not_share_nested_mutable_parameters(self):
        github = get_preset('github')
        github.record.headers['Authorization'] = 'caller change'
        github.record.metadata['oauth']['scopes'].append('caller-scope')
        playwright = get_preset('playwright')
        playwright.record.args.append('caller-argument')
        self.assertEqual(PRESETS['github'].record.headers['Authorization'], 'Bearer ${GITHUB_TOKEN}')
        self.assertEqual(get_preset('github').record.metadata['oauth']['scopes'], [])
        self.assertEqual(get_preset('playwright').record.args, ['@playwright/mcp@latest'])

    def test_custom_preset_fields_equal_to_product_copy_are_not_translated(self):
        original = resources('zh-CN')['python.presets.ea932f93ed9d']
        preset = ConnectorPreset(record=ConnectorRecord(name='external', transport='stdio', command='fixture',
            metadata={'description': original, 'auth_hint': original}), skill_markdown=original)
        with current_language.set('en'):
            self.assertEqual(preset.summary()['description'], original)
            self.assertEqual(preset.summary()['auth_hint'], original)
            self.assertEqual(preset.resolve().skill_markdown, original)


class SkillSourceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_system_discovery_path_does_not_make_file_text_a_product_resource(self):
        original = resources('zh-CN')['python.presets.ea932f93ed9d']
        content = resources('zh-CN')['policy.edit']
        skill = SkillMetadata(name='fixture', description=original, when_to_use=original, content=content,
            path=SYSTEM_SKILL_ROOT / 'fixture' / 'SKILL.md', source='system-harness')
        registry = SimpleNamespace(refresh=lambda: None, get=lambda _: skill, list=lambda: [skill])
        host = SimpleNamespace(skills=registry)
        for language in ('en', 'zh-CN', 'en'):
            with current_language.set(language), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                self.assertIn(original, skill.summary_line())
                self.assertIn(content, render_loaded_skill(skill))
                row = skill_catalog(host)[0]
                self.assertEqual((row['description'], row['when_to_use'], row['content']), (original, original, content))
                tool = build_skill_tool(registry)[0]
                result = await tool.execute({'name': 'fixture'}, SimpleNamespace(services={}, session_id='fixture'))
                self.assertEqual(result.display['description'], original)
                self.assertIn(content, result.content[0].text)

    async def test_real_preset_install_writes_current_skill_once_and_preserves_user_edits(self):
        from harness import Harness, HarnessConfig
        from harness.skills.paths import user_skill_root
        from tests.web_fixture import FixtureTransport
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            flags = {item.name: False for item in fields(HarnessConfig) if item.name.startswith('enable_')}
            flags['enable_connectors'] = True
            host = Harness(HarnessConfig(api_key='fixture', model='fixture', home=root / 'state', cwd=root,
                                         **flags), transport=FixtureTransport())
            try:
                with current_language.set('en'), patch('harness.i18n.localize_text', side_effect=AssertionError('reverse lookup'), create=True):
                    receipt = host.install_connector_preset('github')
                    path = user_skill_root(host.config.home) / 'github' / 'SKILL.md'
                    self.assertEqual(Path(receipt['skill_path']), path)
                    self.assertEqual(path.read_text(encoding='utf-8'), tr(PRESETS['github'].skill_key))
                path.write_text('Original user edit 原文', encoding='utf-8')
                with current_language.set('zh-CN'):
                    receipt = host.install_connector_preset('github')
                    self.assertIsNone(receipt['skill_path'])
                    self.assertEqual(path.read_text(encoding='utf-8'), 'Original user edit 原文')
                    self.assertEqual(receipt['connector']['metadata']['description'], tr(PRESETS['github'].description_key))
            finally:
                await host.close()
