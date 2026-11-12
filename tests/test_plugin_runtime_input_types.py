"""运行时插件入口不能将非文本身份转换为有效名称或对话。"""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from harness.core.language import current_language
from harness.i18n import tr
from harness.web.api.plugins import build_router
from harness.web.errors import BadRequest
from harness.web.router import Request


class PluginRuntimeInputTypesTests(unittest.TestCase):
    def test_mount_and_selection_reject_non_text_identity_before_any_operation(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                for endpoint in ('agent-mount','agent-selection'):
                    for value in (True,False,0,1,[],{},['fixture']):
                        harness=Mock();path='/api/v1/plugins/'+endpoint
                        handler,params=build_router(SimpleNamespace(harness=harness)).resolve('POST',path)
                        request=Request('POST',path,{}, {},json.dumps({'session_id':value,'name':None}).encode())
                        with self.assertRaises(BadRequest) as error:handler(request,**params)
                        self.assertEqual(error.exception.code,'plugin/bad-body')
                        self.assertEqual(error.exception.message,tr('python.plugins.37840a0a4194'))
                        harness.set_agent_mount.assert_not_called();harness.set_agent_selection.assert_not_called()
                        harness.get_session.assert_not_called()

    def test_connector_non_text_names_are_rejected_before_store_or_refresh(self):
        for language in ('en','zh-CN','en'):
            with current_language.set(language):
                for value in (None,True,False,0,1,[],{},['fixture'],'',' '):
                    harness=Mock();path='/api/v1/plugins/connectors'
                    handler,params=build_router(SimpleNamespace(harness=harness)).resolve('POST',path)
                    request=Request('POST',path,{}, {},json.dumps({'name':value}).encode())
                    with self.assertRaises(BadRequest) as error:handler(request,**params)
                    self.assertEqual(error.exception.code,'plugin/bad-body')
                    self.assertEqual(error.exception.message,tr('python.plugins.7650228e2aa8'))
                    harness.connector_store.upsert.assert_not_called();harness.refresh_connectors.assert_not_called()
