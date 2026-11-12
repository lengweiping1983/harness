"""POSIX反斜杠是文件名字符，不能与目录分隔符共享观察权限。"""
import os
from pathlib import Path
import tempfile
import unittest

from harness.core.errors import ToolExecutionError
from harness.session.session import Session
from harness.tools.builtin.fs_observation import ObservationCache, observe_existing, require_observed, target_key


@unittest.skipIf(os.name == 'nt', 'Windows不允许文件名中的反斜杠')
class ObservationPathTests(unittest.TestCase):
    def test_distinct_posix_paths_do_not_share_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            literal = root / 'a\\b.txt'
            nested = root / 'a' / 'b.txt'
            nested.parent.mkdir()
            literal.write_text('same content')
            nested.write_text('same content')
            self.assertNotEqual(target_key(literal), target_key(nested))
            cache = ObservationCache()
            observe_existing('fixture', literal, cache=cache)
            require_observed('fixture', literal, cache=cache)
            with self.assertRaises(ToolExecutionError) as caught:
                require_observed('fixture', nested, cache=cache)
            self.assertEqual(caught.exception.code, 'FS_NOT_OBSERVED')
            session = Session()
            observe_existing(session.id, literal, session=session)
            require_observed(session.id, literal, session=session)
            with self.assertRaises(ToolExecutionError) as caught:
                require_observed(session.id, nested, session=session)
            self.assertEqual(caught.exception.code, 'FS_NOT_OBSERVED')

    def test_root_identity_is_not_empty(self):
        self.assertEqual(target_key(Path('/')), '/')
