"""分页快照按轮次关联工具调用，旧输出不能污染当前运行项。"""
import unittest

from harness.session.events import AppendOp
from harness.session.session import Session
from harness.web.conversation import project_page
from harness.web.views import _project_messages


class ConversationCallIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_results_and_running_output_keep_their_round_across_pages(self):
        session = Session()
        for turn in range(1, 4):
            await session.start_turn()
            await session.append('assistant/message', {'content': [
                {'type': 'tool_use', 'id': 'same', 'name': f'tool{turn}', 'input': {'path': f'file{turn}'}}]}, surface_op=AppendOp())
            await session.append('tool/call', {'call_id': 'same', 'turn': turn, 'status': 'running',
                'name': f'tool{turn}', 'input': {'path': f'file{turn}'}})
            await session.append('tool/output', {'call_id': 'same', 'turn': turn, 'output_seq': 1,
                'stdout': f'output{turn}', 'stderr': '', 'stdout_offset': 0, 'stderr_offset': 0})
            if turn < 3:
                await session.append('tool/result', {'call_id': 'same', 'turn': turn, 'name': f'tool{turn}',
                    'results': [{'tool_use_id': 'same', 'content': f'result{turn}', 'is_error': False}]}, surface_op=AppendOp())
                await session.append('turn/end', {'turn': turn, 'reason': {'kind': 'completed'}})
        await session.append('tool/output', {'call_id': 'same', 'turn': 1, 'output_seq': 2,
            'stdout': 'late', 'stderr': '', 'stdout_offset': 7, 'stderr_offset': 0})
        projected = _project_messages(session, active_turn=3)
        projected_results = [result for message in projected for result in message.get('results', [])]
        self.assertEqual([result['name'] for result in projected_results], ['tool1', 'tool2'])
        for limit in (1, 100):
            with self.subTest(limit=limit):
                cursor = None
                results = []
                while True:
                    messages, events, history = project_page(session, list(session.events), limit=limit,
                        cursor=cursor, active_turn=3)
                    results.extend(result for message in messages for result in message.get('results', []))
                    if cursor is None:
                        running = [event for event in events if event['type'] == 'tool/call']
                        output = [event for event in events if event['type'] == 'tool/output']
                        self.assertEqual([event['data']['turn'] for event in running], [3])
                        self.assertEqual([event['data']['turn'] for event in output], [3])
                        self.assertEqual(output[0]['data']['stdout'], 'output3')
                    cursor = history['next_cursor']
                    if cursor is None:
                        break
                results.sort(key=lambda result: result['turn'])
                self.assertEqual([result['turn'] for result in results], [1, 2])
                self.assertEqual([result['input'] for result in results], [{'path': 'file1'}, {'path': 'file2'}])
                self.assertEqual([result['progress']['stdout'] for result in results], ['output1', 'output2'])
