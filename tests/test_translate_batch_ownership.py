"""批量工具结果与延后消息的投影不修改原始输入或泄漏局部状态。"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from harness.core.language import current_language
from harness.llm.translate import messages_to_wire
from harness.llm.types import AssistantMessage, ToolUseBlock, ToolResultMessage, ToolResultBlock, UserMessage


class TranslateBatchOwnershipTests(unittest.TestCase):
    def test_deferred_message_follows_the_whole_batch_even_when_results_arrive_separately(self):
        messages=[AssistantMessage([ToolUseBlock('first','read',{'path':'Original'}),ToolUseBlock('second','read',{'path':'Other'})]),
                  ToolResultMessage([ToolResultBlock('first','Original result 原文')]),
                  UserMessage('Original steering 原文'),
                  ToolResultMessage([ToolResultBlock('second','Original error 原文',True)])]
        original=deepcopy(messages)
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                wire=messages_to_wire(messages)
                self.assertEqual([item['role'] for item in wire],['assistant','tool','tool','user'])
                self.assertEqual([wire[1]['tool_call_id'],wire[2]['tool_call_id']],['first','second'])
                self.assertEqual(wire[-1]['content'],'Original steering 原文')
                self.assertIn('Original error 原文',wire[2]['content'])
                self.assertEqual(messages,original)

    def test_mid_batch_projection_failure_retains_original_error_and_next_call_is_clean(self):
        messages=[AssistantMessage([ToolUseBlock('owned','read',{})]),UserMessage('Original deferred'),ToolResultMessage([ToolResultBlock('owned','Original')])]
        original=deepcopy(messages)
        from harness.llm import translate
        convert=translate.message_to_wire
        failure=OSError('Original projection failure 原文')
        def project(message,**kwargs):
            if isinstance(message,ToolResultMessage):raise failure
            return convert(message,**kwargs)
        with patch.object(translate,'message_to_wire',side_effect=project):
            with self.assertRaises(OSError) as error:messages_to_wire(messages)
        self.assertIs(error.exception,failure)
        self.assertEqual(messages,original)
        fresh=messages_to_wire([UserMessage('Fresh 原文')])
        self.assertEqual(fresh,[{'role':'user','content':'Fresh 原文'}])
