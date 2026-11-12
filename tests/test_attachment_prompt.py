"""附件围栏不得被正文提前闭合，语言标识只放在开头。"""
import unittest

from harness.core.language import current_language
from harness.web.prompt import AttachmentPart, render_part


class AttachmentPromptTests(unittest.TestCase):
    def test_fenced_attachments_close_without_language_hint(self):
        for language in ('zh-CN', 'en'):
            for media_type, hint in (('text/markdown', 'markdown'), ('application/json', 'json')):
                with self.subTest(language=language, media_type=media_type):
                    body = 'before\n```\ninside\n`````\nafter'
                    with current_language.set(language):
                        rendered = render_part(AttachmentPart('fixture', media_type, len(body), text=body))
                    self.assertTrue(rendered.endswith('\n``````'))
                    self.assertIn('\n``````' + hint + '\n' + body + '\n', rendered)
