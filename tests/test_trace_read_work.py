"""请求详情的大输入、原文分页与并发观察回归。"""
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from harness.llm.prompt_cache import _shared_text_prefix, compare_prefix
from harness.web.request_traces import read_trace
from harness.web.read_work import shared_read


class TraceReadWorkTests(unittest.TestCase):
    def test_prefix_matches_unicode_reference_and_block_boundaries(self):
        """分块与二分仍返回精确 Unicode 字符偏移。"""
        for size in [0, 1, 65535, 65536, 65537, 200000]:
            prefix = '中文😀a' * (size // 4) + 'x' * (size % 4)
            for old, new in [(prefix, prefix), (prefix+'a', prefix+'b'), (prefix, prefix+'a'), ('a'+prefix, 'b'+prefix)]:
                expected = next((i for i, (a,b) in enumerate(zip(old,new)) if a != b), min(len(old),len(new)))
                self.assertEqual(_shared_text_prefix(old,new), expected)
        a = {'model':'fixture','messages':[{'role':'user','content':'中😀'*100000}]}
        b = {**a,'messages':a['messages']+[{'role':'user','content':'next'}]}
        self.assertEqual(compare_prefix(a,b)['shared_messages'],1)

    def test_raw_page_does_not_parse_or_inspect(self):
        """原始正文不依赖诊断解析，分页保留末尾字符。"""
        body = '中😀' * 24001
        archive = SimpleNamespace(read=lambda *args: ({'session_id':'owner'}, body))
        # 真实归档对象可弱引用且可哈希。
        class Archive:
            def read(self,*args): return archive.read(*args)
        with patch('harness.web.request_traces.json.loads', side_effect=AssertionError('不应解析')):
            result = read_trace(Archive(),'owner','trace',part='body',offset=24000)
        self.assertEqual(result['text'],body[24000:48000])
        self.assertEqual(result['total_chars'],len(body))

    def test_concurrent_read_is_shared_and_failure_is_not_cached(self):
        """在途计算只执行一次，错误不阻止后续显式读取。"""
        class Owner: pass
        owner = Owner()
        entered, release = Event(), Event()
        calls = []
        def read():
            calls.append(1); entered.set(); self.assertTrue(release.wait(5)); return {'value':1}
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(shared_read,owner,'key',read)
            self.assertTrue(entered.wait(5))
            # 屏障确认第二等待者取得同一个 Future 后才释放首读。
            from harness.web import read_work
            original = read_work.Future.result
            waiting = Event()
            def result(future,*args,**kwargs):
                waiting.set(); return original(future,*args,**kwargs)
            with patch.object(read_work.Future,'result',result):
                second = pool.submit(shared_read,owner,'key',read)
                self.assertTrue(waiting.wait(5)); release.set()
                self.assertEqual(first.result(5),second.result(5))
        self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(ValueError,'original'):
            shared_read(owner,'bad',lambda: (_ for _ in ()).throw(ValueError('original')))
        self.assertEqual(shared_read(owner,'bad',lambda: 2),2)

    def test_distinct_scope_does_not_share_results(self):
        """不同来源和对话不共享在途结果。"""
        class Owner: pass
        owner=Owner()
        self.assertEqual(shared_read(owner,('A','trace'),lambda:'A'),'A')
        self.assertEqual(shared_read(owner,('B','trace'),lambda:'B'),'B')

    def test_unknown_section_reports_protocol_identity(self):
        """未知部分返回机器身份与支持清单，不靠译文判断错误。"""
        class Archive:
            def read(self, *args): return {}, '{}'
        from harness.web.errors import BadRequest
        with self.assertRaises(BadRequest) as caught:
            read_trace(Archive(), 'owner', 'trace', part='unsupported')
        self.assertEqual(caught.exception.code, 'request-trace/unsupported-part')
        self.assertEqual(caught.exception.details['part'], 'unsupported')
        self.assertIn('summary', caught.exception.details['supported'])

    def test_summary_does_not_wait_for_previous_or_history(self):
        """首屏不加载上一请求或会话历史，完整诊断仍保留真实计算。"""
        class Archive:
            def read(self,*args):
                return {'session_id':'owner'}, '{"messages":[{"role":"user","content":"原文😀"}]}'
            def previous(self,*args):
                raise AssertionError('首屏不应读取上一请求')
        sessions=SimpleNamespace(get=lambda *_: self.fail('首屏不应加载历史'))
        detail=read_trace(Archive(),'owner','trace',part='summary',sessions=sessions)
        self.assertEqual(detail['messages'][0]['preview'],'原文😀')
        self.assertIsNone(detail['cache_comparison'])

    def test_full_diagnostics_share_one_surface_and_preserve_sources(self):
        """两类来源复用一次快照投影，工具来源仍按真实调用关联。"""
        from harness.session.events import SessionEvent, AppendOp
        from harness.session.session import project_surface
        events = [
            SessionEvent(seq=0, time=1, type='assistant/message', data={'content':[{'type':'tool_use','id':'call','name':'read'}]}, surface_op=AppendOp()),
            SessionEvent(seq=1, time=2, type='tool/result', data={'results':[{'tool_use_id':'call','content':'Original result'}]}, surface_op=AppendOp()),
        ]
        class Archive:
            def read(self,*args):
                return {'session_id':'owner','context_checkpoint':{'event_seq':1}}, json.dumps({'messages':[{'role':'tool','tool_call_id':'call','content':'Original result'}]})
            def previous(self,*args): return None
        owner=SimpleNamespace(events=events)
        with patch('harness.session.session.project_surface',wraps=project_surface) as projected:
            detail=read_trace(Archive(),'owner','trace',sessions=SimpleNamespace(get=lambda *_:owner))
        projected.assert_called_once()
        self.assertEqual(detail['messages'][0]['tool_sources'][0]['call_id'],'call')
        self.assertEqual(detail['messages'][0]['tool_sources'][0]['event_seq'],1)


if __name__ == '__main__':
    unittest.main()
