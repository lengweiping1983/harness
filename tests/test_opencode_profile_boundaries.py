"""OpenCode 本机资料在解析前限制大小，拒绝重复字段和过深结构。"""
import io
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.core.errors import LlmError
from harness.core.language import current_language
from harness.i18n import tr
from harness.llm.opencode_local import configured_transport, load_profile


class OpenCodeProfileBoundariesTests(unittest.TestCase):
    def test_invalid_profile_bytes_are_localized_and_preserved(self):
        for language in ('en','zh-CN','en'):
            for kind in ('large','nested','duplicate','surrogate','control'):
                with self.subTest(language=language,kind=kind), tempfile.TemporaryDirectory() as directory, current_language.set(language):
                    root=Path(directory);executable=root/'runtime';executable.write_bytes(b'fixture')
                    profile=root/'profile.json'
                    valid={'version':1,'executable_sha256':hashlib.sha256(b'fixture').hexdigest(),'headers':{'user-agent':'Original','x-opencode-client':'fixture','x-opencode-project':'fixture'},'system':[{'role':'system','content':'Original 原文'}]}
                    text=json.dumps(valid)
                    if kind=='large':text=' '* (1024*1024+1)+text
                    elif kind=='nested':text='['*2000+'0'+']'*2000
                    elif kind=='duplicate':text=text.replace('"version": 1','"version": 2, "version": 1')
                    elif kind=='surrogate':valid['system'][0]['content']='\ud800';text=json.dumps(valid)
                    else:valid['headers']['user-agent']='fixture\x00';text=json.dumps(valid)
                    raw=text.encode();profile.write_bytes(raw)
                    with self.assertRaises(LlmError) as error:
                        load_profile(executable,profile)
                    self.assertEqual(error.exception.code,'llm/opencode-local-profile')
                    self.assertEqual(error.exception.message,tr('ui.llm.opencodeLocalProfileInvalid',profile=str(profile)))
                    self.assertEqual(profile.read_bytes(),raw)

    def test_configured_path_uses_same_bounded_reader_before_resolving_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'profile.json';raw=b' '*(1024*1024+1);path.write_bytes(raw)
            with patch.dict('os.environ',{'HARNESS_OPENCODE_EXECUTABLE':'','HARNESS_OPENCODE_PROFILE':str(path)}):
                with self.assertRaises(LlmError) as error:
                    configured_transport()
            self.assertEqual(error.exception.code,'llm/opencode-local-profile')
            self.assertEqual(path.read_bytes(),raw)

    def test_valid_original_context_and_extension_fields_are_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);executable=root/'runtime';executable.write_bytes(b'fixture')
            profile=root/'profile.json'
            data={'version':1,'executable_sha256':hashlib.sha256(b'fixture').hexdigest(),'headers':{'user-agent':'Original','x-opencode-client':'fixture','x-opencode-project':'fixture'},'system':[{'role':'system','content':'Original 原文'}],'vendor':{'original':['原文']}}
            raw=json.dumps(data,ensure_ascii=False).encode();profile.write_bytes(raw)
            self.assertEqual(load_profile(executable,profile),data)
            self.assertEqual(profile.read_bytes(),raw)

    def test_profile_limit_is_applied_to_the_read_before_json_parsing(self):
        class Source(io.BytesIO):
            requested = None
            def read(self, size=-1):
                self.requested = size
                return super().read(size)
        source=Source(b' '*(1024*1024+2))
        with patch.object(Path,'open',return_value=source), patch('harness.llm.opencode_local.json.loads',side_effect=AssertionError('unbounded parse')) as parse:
            with self.assertRaises(LlmError):
                load_profile(Path('/fixture/runtime'),Path('/fixture/profile.json'))
        self.assertEqual(source.requested,1024*1024+1)
        self.assertTrue(source.closed)
        parse.assert_not_called()
