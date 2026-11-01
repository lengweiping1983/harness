"""服务根注册释放旧实例，插件嵌套与异常退出仍恢复原服务。"""
import gc
import unittest
import weakref

from harness.core.events import Context


class Service:
    """可弱引用的隔离服务，不持有外部资源。"""


class ContextServiceLifetimeTests(unittest.TestCase):
    def test_root_replacement_releases_all_previous_services(self):
        context = Context()
        references = []
        for _ in range(100):
            service = Service()
            references.append(weakref.ref(service))
            self.assertIs(context.set('service', service), service)
        del service
        gc.collect()
        self.assertTrue(all(reference() is None for reference in references[:-1]))
        self.assertIs(context.require('service'), references[-1]())
        self.assertIs(context.service, references[-1]())

    def test_nested_overrides_and_exception_restore_exact_services(self):
        context = Context()
        root = Service()
        context.set('service', root)
        with context.plugin('outer'):
            outer = Service()
            context.set('service', outer)
            context.set('outer-only', 'original')
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                with context.plugin('inner'):
                    context.set('service', Service())
                    context.set('service', Service())
                    context.set('outer-only', 'changed')
                    context.set('inner-only', Service())
                    self.assertIsNot(context.require('service'), outer)
                    raise RuntimeError('fixture')
            self.assertIs(context.require('service'), outer)
            self.assertEqual(context.require('outer-only'), 'original')
            self.assertFalse(context.has('inner-only'))
        self.assertIs(context.require('service'), root)
        self.assertNotIn('outer-only', context)

    def test_root_can_replace_restored_service_without_retaining_undo_history(self):
        context = Context()
        original = Service()
        reference = weakref.ref(original)
        context.set('service', original)
        with context.plugin():
            context.set('service', Service())
        self.assertIs(context.require('service'), original)
        del original
        replacement = Service()
        context.set('service', replacement)
        gc.collect()
        self.assertIsNone(reference())
        self.assertIs(context.require('service'), replacement)
