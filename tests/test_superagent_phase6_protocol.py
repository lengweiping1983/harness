"""设备配置、公共协议源及模板导出保持当前身份，不读写用户凭据。"""
import json
from pathlib import Path
import tempfile
import unittest

from harness.app.superagent_config import read_config,config_identity
from harness.app.superagent_coordinator_store import _hash,_json
from harness.app.superagent_remote_protocol import worker_openapi,frame,encode,decode,validate
from harness.core.errors import HarnessError
from harness.superagent_worker import export_worker_config
from harness.tools.schema import validate as validate_schema
from tests.superagent_product_fixture import product_config


class RemoteProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path,self.raw=product_config(self.root)
        self.raw['execution']={'devices':[{'device_id':'a','url':'https://device.invalid','key_ref':'secret://a','capacity':2}],'lease_seconds':30}
        self.raw['secrets']={'a':'FIXTURE_DEVICE_KEY'};self.path.write_text(json.dumps(self.raw))

    def test_device_template_binds_exact_coordinator_identity_and_never_overwrites_or_exports_credentials(self):
        output=self.root/'worker.json'
        value=export_worker_config(self.path,'a',output)
        spec=read_config(self.path)
        self.assertEqual(value['identity_hash'],_hash(_json(config_identity(spec))))
        self.assertEqual(value['key_env'],'FIXTURE_DEVICE_KEY')
        self.assertEqual(value['runner'],'docker')
        self.assertIn('REQUIRED_DIGEST',value['sandbox']['image'])
        self.assertFalse((self.root/'state').exists())
        original=output.read_bytes()
        with self.assertRaises(FileExistsError):export_worker_config(self.path,'a',output)
        self.assertEqual(output.read_bytes(),original)

    def test_duplicate_device_keys_plaintext_remote_http_and_old_or_foreign_frames_are_rejected(self):
        invalid=[{**self.raw['execution']['devices'][0],'device_id':'b'},
            {**self.raw['execution']['devices'][0],'url':'http://device.invalid','device_id':'b','key_ref':'secret://b'}]
        for device in invalid:
            raw={**self.raw,'execution':{**self.raw['execution'],'devices':[self.raw['execution']['devices'][0],device]}}
            self.path.write_text(json.dumps(raw))
            with self.subTest(device=device),self.assertRaises(HarnessError):read_config(self.path)
        value=frame(instance_id='instance',device_id='device',identity_hash='a'*64,operation='identity',params={})
        for change in ({'issued_at':0},{'device_id':'foreign'},{'version':True},{'nonce':'short'}):
            with self.subTest(change=change),self.assertRaises(HarnessError):validate({**value,**change},instance_id='instance',device_id='device',identity_hash='a'*64)
        with self.assertRaises(HarnessError):decode(b'{"version":1,"version":2}')

    def test_generated_worker_contract_matches_actual_frame_and_all_executor_operations(self):
        document=worker_openapi()
        stored=json.loads((Path(__file__).resolve().parents[1]/'docs/openapi-worker-v1.json').read_text())
        self.assertEqual(stored,document)
        value=frame(instance_id='instance',device_id='device',identity_hash='a'*64,operation='identity',params={})
        self.assertEqual(validate_schema(decode(encode(value)),document['components']['schemas']['WorkerFrame']),[])
        self.assertIn('reconcile_instruction',document['components']['schemas']['WorkerFrame']['properties']['operation']['enum'])
        self.assertEqual(document['components']['schemas']['WorkerIdentity']['properties']['runner']['enum'],['native','docker'])

    def test_single_native_device_export_has_no_docker_requirement_and_rejects_invalid_runner_or_empty_devices(self):
        from harness.superagent_worker import read_worker_config
        example=read_config(Path(__file__).resolve().parents[1]/'examples/superagent-native.json')
        self.assertEqual(len(example['execution']['devices']),1)
        self.assertEqual(example['execution']['devices'][0]['runner'],'native')
        self.raw['execution']['devices'][0]['runner']='native'
        self.path.write_text(json.dumps(self.raw))
        output=self.root/'native.json';value=export_worker_config(self.path,'a',output)
        self.assertEqual(value['runner'],'native');self.assertIsNone(value['sandbox'])
        value['host_state']=str(self.root)
        for profile in value['profiles'].values():profile['repo']=str(self.root/'repo')
        output.write_text(json.dumps(value))
        self.assertEqual(read_worker_config(output)['runner'],'native')
        for change in ({'devices':[]},{'devices':[{**self.raw['execution']['devices'][0],'runner':'automatic'}]},
                       {'devices':[{**self.raw['execution']['devices'][0],'runner':{}}]}):
            self.path.write_text(json.dumps({**self.raw,'execution':{**self.raw['execution'],**change}}))
            with self.subTest(change=change),self.assertRaises(HarnessError):read_config(self.path)

    def test_different_device_secret_references_cannot_resolve_to_the_same_signing_key(self):
        from harness.app.superagent_remote_client import RemoteExecutionAdapter
        spec={'execution':{'devices':[{'device_id':'a','key_ref':'secret://a'},{'device_id':'b','key_ref':'secret://b'}]}}
        with self.assertRaises(HarnessError) as caught:
            RemoteExecutionAdapter(store=None,spec=spec,secret_resolver=lambda ref:b'same-credential-for-two-devices-32bytes')
        self.assertEqual(caught.exception.code,'superagent/remote.identity')

    def test_invalid_worker_config_uses_explicit_error_and_preserves_original_bytes(self):
        from harness.superagent_worker import read_worker_config
        path=self.root/'invalid-worker.json';original=b'{"version":1,"version":2}'
        path.write_bytes(original)
        with self.assertRaises(HarnessError) as caught:read_worker_config(path)
        self.assertEqual(caught.exception.code,'superagent/remote.config')
        self.assertEqual(path.read_bytes(),original)


class GatewayAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_stalled_tls_client_does_not_block_admission_of_the_next_connection(self):
        import asyncio
        import socket
        import ssl
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from harness.app.superagent_remote_gateway import WorkerGateway
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        node=SimpleNamespace(close=AsyncMock())
        gateway=WorkerGateway(node,instance_id='instance',device_id='device',identity_hash='a'*64,key=b'k'*32,tls=context)
        stalled=socket.create_connection(('127.0.0.1',gateway.port),timeout=2)
        def connect_again():
            client=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT);client.check_hostname=False;client.verify_mode=ssl.CERT_NONE
            with socket.create_connection(('127.0.0.1',gateway.port),timeout=2) as connection:
                # 此夹具没有证书，应立即返回 TLS 拒绝，不能等待前一连接握手超时。
                with self.assertRaises(ssl.SSLError):
                    with client.wrap_socket(connection,server_hostname='localhost'):pass
        try:await asyncio.to_thread(connect_again)
        finally:
            stalled.close();await gateway.close()
        node.close.assert_awaited_once()
