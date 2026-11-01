"""图片投影、容量与预算使用具体结果位置，不能把重复调用 ID 当成全局身份。"""
import unittest

from harness.core.language import current_language
from harness.llm.context_accounting import context_composition
from harness.llm.translate import messages_to_wire
from harness.llm.types import AssistantMessage, ToolResultBlock, ToolResultMessage, ToolUseBlock
from harness.llm.vision import IMAGE_INPUT_TOKEN_ESTIMATE, image_projection_plan


def exchange(name, text):
    return [AssistantMessage(content=(ToolUseBlock(id='same', name=name, input={}),)),
            ToolResultMessage(results=(ToolResultBlock(tool_use_id='same', content=text),))]


def urls(wire):
    return [part['image_url']['url'] for item in wire if isinstance(item.get('content'), list)
            for part in item['content'] if part.get('type') == 'image_url']


def image_tokens(messages):
    return sum(row['tokens'] for row in context_composition(messages)['sources'] if row['category'] == 'images')


class ImageCallIdentityTests(unittest.TestCase):
    def test_plain_tool_reusing_image_id_is_not_promoted_in_either_order(self):
        image = 'data:image/png;base64,AAAA'
        plain = 'data:image/png;base64,BBBB'
        for language in ('en', 'zh-CN'):
            for names in (('read_image', 'shell'), ('shell', 'read_image')):
                with self.subTest(language=language, names=names), current_language.set(language):
                    messages = [message for name in names for message in exchange(name, image if name == 'read_image' else plain)]
                    for native in (False, True):
                        wire = messages_to_wire(messages, native_responses=native)
                        self.assertEqual(urls(wire), [image])
                        self.assertIn(plain, [item.get('content') for item in wire])
                    self.assertEqual(image_tokens(messages), IMAGE_INPUT_TOKEN_ESTIMATE)

    def test_distinct_images_with_reused_id_obey_eight_image_budget(self):
        images = ['data:image/png;base64,' + chr(65 + index) * 4 for index in range(10)]
        messages = [message for image in images for message in exchange('read_image', image)]
        eligible, retained, repeated = image_projection_plan(messages)
        self.assertEqual(len(eligible), 10)
        self.assertEqual(len(retained), 8)
        self.assertEqual(repeated, {})
        for native in (False, True):
            self.assertEqual(urls(messages_to_wire(messages, native_responses=native)), images[-8:])
        self.assertEqual(image_tokens(messages), 8 * IMAGE_INPUT_TOKEN_ESTIMATE)

    def test_repeated_image_with_reused_id_is_emitted_once(self):
        image = 'data:image/png;base64,AAAA'
        messages = [message for _ in range(10) for message in exchange('read_image', image)]
        eligible, retained, repeated = image_projection_plan(messages)
        self.assertEqual(len(eligible), 10)
        self.assertEqual(retained, {(1, 0)})
        self.assertEqual(len(repeated), 9)
        for language in ('en', 'zh-CN'):
            with self.subTest(language=language), current_language.set(language):
                for native in (False, True):
                    wire = messages_to_wire(messages, native_responses=native)
                    self.assertEqual(urls(wire), [image])
                    tool_texts = [item['content'] for item in wire if item['role'] == 'tool']
                    self.assertEqual(len(tool_texts), 10)
                    self.assertTrue(all('base64,' not in text for text in tool_texts))
                self.assertEqual(image_tokens(messages), IMAGE_INPUT_TOKEN_ESTIMATE)

    def test_batched_results_keep_positions_and_close_calls_before_images(self):
        image = 'data:image/png;base64,AAAA'
        plain = 'data:image/png;base64,BBBB'
        messages = [AssistantMessage(content=(
            ToolUseBlock(id='picture', name='read_image', input={}),
            ToolUseBlock(id='plain', name='shell', input={}))),
            ToolResultMessage(results=(ToolResultBlock(tool_use_id='plain', content=plain),
                                       ToolResultBlock(tool_use_id='picture', content=image)))]
        self.assertEqual(image_projection_plan(messages)[0], {(1, 1)})
        wire = messages_to_wire(messages)
        self.assertEqual([item['role'] for item in wire], ['assistant', 'tool', 'tool', 'user'])
        self.assertEqual(wire[1]['content'], plain)
        self.assertEqual(urls(wire), [image])
        self.assertEqual(image_tokens(messages), IMAGE_INPUT_TOKEN_ESTIMATE)
