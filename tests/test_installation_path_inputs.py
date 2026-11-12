"""显式空路径或非路径类型不能使SDK安装回退到真实用户或项目目录。"""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from harness.core.errors import ValidationError
from harness.core.language import current_language
from harness.i18n import tr
from harness.plugins.installer import install_from_directory, uninstall
from harness.skills.paths import installation_root


class InstallationPathInputsTests(unittest.TestCase):
    def test_invalid_scope_paths_fail_without_creating_directories(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                for field in ('cwd','home'):
                    for value in ('',' ',True,False,0,[],{}):
                        with self.subTest(field=field,value=value),self.assertRaises(ValidationError) as error:
                            installation_root('skill','project',**{field:value})
                        self.assertEqual(error.exception.message,tr('plugin.installation.invalidPath',field=field))
                self.assertEqual(list(root.iterdir()),[])

    def test_invalid_source_and_uninstall_name_fail_before_filesystem_mutation(self):
        with patch('harness.plugins.installer.shutil.copytree') as copy,patch('harness.plugins.installer.shutil.rmtree') as remove:
            for value in (None,'',' ',True,False,0,[],{}):
                with self.subTest(source=value),self.assertRaises(ValidationError):install_from_directory(value,kind='skill')
            for value in (None,True,False,0,[],{}):
                with self.subTest(name=value),self.assertRaises(ValidationError):uninstall(value,kind='agent')
            copy.assert_not_called();remove.assert_not_called()

    def test_explicit_relative_path_and_omitted_default_keep_consistent_roots(self):
        expected=(Path.cwd()/'relative-project').resolve()/'.agents/skills'
        self.assertEqual(installation_root('skill','project',cwd='relative-project'),expected)
        self.assertEqual(installation_root('skill','project'),Path.cwd()/'.agents/skills')
