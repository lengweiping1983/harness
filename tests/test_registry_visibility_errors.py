"""查找错误的可用列表遵守当前启用过滤，管理目录不删除原条目。"""
import unittest

from harness.agents.parser import AgentDefinition
from harness.agents.registry import AgentRegistry
from harness.skills.parser import SkillMetadata
from harness.skills.registry import SkillRegistry
from harness.core.errors import AgentNotFoundError, SkillNotFoundError
from harness.core.language import current_language
from harness.i18n import tr


class RegistryVisibilityErrorTests(unittest.TestCase):
    def registries(self):
        for kind, factory, definition, error in (
            ('agent', AgentRegistry, AgentDefinition, AgentNotFoundError),
            ('skill', SkillRegistry, lambda name, description: SkillMetadata(name, description, 'Original body 原文'), SkillNotFoundError),
        ):
            registry = factory()
            for name in ('visible', 'disabled-one', 'disabled-two'):
                registry.register(definition(name, 'Original description 原文'))
            yield kind, registry, error

    def assert_error(self, kind, registry, error, requested, available, blocked=False):
        with self.assertRaises(error) as caught:
            registry.get(requested)
        key = 'python.registry.cb8d3978ce07' if kind == 'agent' else 'python.registry.43e3300e2843'
        self.assertEqual(caught.exception.message, tr(key, p0=repr(requested),
            p1=tr('python.registry.a8c3698b5b8c' if blocked else 'python.registry.4e51b67dd32c'), p2=available))

    def test_missing_lookup_only_advertises_visible_items_in_each_language(self):
        for kind, registry, error in self.registries():
            registry.set_enabled_filter(lambda item: item.name == 'visible')
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(kind=kind, language=language), current_language.set(language):
                    self.assert_error(kind, registry, error, 'missing', 'visible')
                    self.assertEqual([row.name for row in registry.list()], ['visible'])
                    self.assertEqual([row.name for row in registry.list_all()], ['disabled-one', 'disabled-two', 'visible'])
                    self.assertTrue(registry.has('disabled-one'))

    def test_blocked_lookup_keeps_requested_identity_without_listing_other_hidden_items(self):
        for kind, registry, error in self.registries():
            registry.set_enabled_filter(lambda item: item.name == 'visible')
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(kind=kind, language=language), current_language.set(language):
                    self.assert_error(kind, registry, error, 'disabled-one', 'visible', blocked=True)

    def test_all_hidden_and_filter_changes_are_resolved_at_call_time(self):
        for kind, registry, error in self.registries():
            registry.set_enabled_filter(lambda _: False)
            for language in ('en', 'zh-CN', 'en'):
                with self.subTest(kind=kind, language=language), current_language.set(language):
                    self.assert_error(kind, registry, error, 'missing', tr('python.registry.f4b39d9b4542'))
            registry.set_enabled_filter(lambda item: item.name == 'disabled-two')
            self.assert_error(kind, registry, error, 'missing', 'disabled-two')
            self.assertEqual(registry.get('disabled-two').name, 'disabled-two')
            registry.set_enabled_filter(None)
            self.assert_error(kind, registry, error, 'missing', 'disabled-one, disabled-two, visible')
