"""预设配套技能只公开完整新文件，不覆盖并发写入或已有链接。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from harness.app.harness import Harness
from harness.core.language import current_language
from harness.connectors.presets import get_preset
from harness.skills.paths import user_skill_root


class PresetSkillCreationTests(unittest.TestCase):
    def harness(self, home):
        harness = object.__new__(Harness)
        harness.connectors = object()
        harness.connector_store = SimpleNamespace(upsert=lambda record: record)
        harness.config = SimpleNamespace(home=home)
        harness.refresh_skills = Mock()
        return harness

    def test_competing_writer_between_check_and_publication_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = user_skill_root(home)/'github'/'SKILL.md'
            harness = self.harness(home)
            mkdir = Path.mkdir

            def competing(path, *args, **kwargs):
                result = mkdir(path, *args, **kwargs)
                if path == target.parent and not target.exists():
                    target.write_bytes(b'Original user skill {literal}')
                return result

            with patch.object(Path,'mkdir',competing):
                result = harness.install_connector_preset('github')
            self.assertEqual(target.read_bytes(),b'Original user skill {literal}')
            self.assertIsNone(result['skill_path'])
            harness.refresh_skills.assert_called_once()
            self.assertEqual(list(target.parent.iterdir()),[target])

    def test_fresh_install_uses_call_language_and_preserves_existing_user_file(self):
        for language in ('en','zh-CN','en'):
            with tempfile.TemporaryDirectory() as directory, current_language.set(language):
                home = Path(directory)
                harness = self.harness(home)
                result = harness.install_connector_preset('github')
                target = Path(result['skill_path'])
                self.assertEqual(target.read_text(encoding='utf-8'),get_preset('github').skill_markdown)
                target.write_bytes(b'Original user skill')
                self.assertIsNone(harness.install_connector_preset('github')['skill_path'])
                self.assertEqual(target.read_bytes(),b'Original user skill')
                self.assertEqual(list(target.parent.iterdir()),[target])

    def test_publication_failure_retains_user_data_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            harness = self.harness(home)
            failure = PermissionError('Original publication failure')
            with patch('os.link',side_effect=failure):
                with self.assertRaises(PermissionError) as caught:
                    harness.install_connector_preset('github')
            self.assertIs(caught.exception,failure)
            self.assertEqual(list((user_skill_root(home)/'github').iterdir()),[])
            harness.refresh_skills.assert_not_called()
