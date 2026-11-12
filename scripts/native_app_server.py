"""Bounded native command readiness transport; never approves server requests."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


def execute_probe(binary: Path, cwd: Path, command: list[str], *, mode: str = 'elevated',
                  timeout: float = 30, popen: Any = subprocess.Popen) -> dict[str, Any]:
    if mode not in {'elevated', 'unelevated'}:
        raise ValueError('unknown Windows sandbox mode')
    if not binary.is_absolute() or not cwd.is_absolute() or not command or any(not isinstance(arg, str) for arg in command):
        raise ValueError('probe requires absolute binary/cwd and a nonempty argv vector')
    if not 0 < timeout <= 120:
        raise ValueError('probe timeout must be between 0 and 120 seconds')
    messages: queue.Queue = queue.Queue()
    stderr: list[str] = []
    process_id = 'harness-probe-' + uuid.uuid4().hex
    process = popen([str(binary), 'app-server', '--stdio', '-c', f'windows.sandbox="{mode}"'],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8', errors='strict',
                    **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
    def read():
        try:
            for line in process.stdout:
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict): raise ValueError('non-object RPC message')
                    messages.put(message)
                except ValueError as error:
                    messages.put(error)
        finally:
            messages.put(EOFError('native app server closed its reply stream'))
    def read_errors():
        for line in process.stderr:
            stderr.append(line)
            while sum(map(len, stderr)) > 2000 and len(stderr) > 1: stderr.pop(0)
    readers = [threading.Thread(target=read, daemon=True), threading.Thread(target=read_errors, daemon=True)]
    for reader in readers: reader.start()
    def send(message):
        process.stdin.write(json.dumps(message) + '\n'); process.stdin.flush()
    def response(request_id, deadline):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError('native app server reply timed out')
            try: message = messages.get(timeout=remaining)
            except queue.Empty: raise TimeoutError('native app server reply timed out') from None
            if isinstance(message, Exception): raise message
            if 'method' in message:
                if 'id' in message:
                    send({'id': message['id'], 'error': {'code': -32601, 'message': 'Readiness probe cannot approve or service server requests'}})
                continue
            if type(message.get('id')) is not int or message['id'] != request_id: continue
            if 'error' in message:
                if not isinstance(message['error'], dict): raise ValueError('invalid native RPC error')
                raise RuntimeError(str(message['error'].get('message', 'native RPC failed')))
            if not isinstance(message.get('result'), dict): raise ValueError('invalid native RPC result')
            return message['result']
    result: dict[str, Any] = {'completed': False, 'mode': mode}
    command_sent = False
    try:
        deadline = time.monotonic() + timeout
        send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'harness_readiness', 'version': '0.1'}, 'capabilities': {'experimentalApi': True}}})
        response(1, deadline)
        send({'method': 'initialized'})
        send({'id': 2, 'method': 'command/exec', 'params': {
            'command': command, 'cwd': str(cwd), 'processId': process_id,
            'timeoutMs': max(1, min(10000, int(timeout * 1000))),
            'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': [str(cwd)],
                              'networkAccess': False, 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True}}})
        command_sent = True
        reply = response(2, deadline)
        if type(reply.get('exitCode')) is not int or not all(isinstance(reply.get(field), str) for field in ('stdout', 'stderr')):
            raise ValueError('incomplete native command receipt')
        result.update(completed=True, command=reply)
    except (OSError, ValueError, EOFError, RuntimeError, TimeoutError) as error:
        result['error'] = str(error)[:2000]
    finally:
        if command_sent and not result['completed'] and process.poll() is None:
            try:
                send({'id': 3, 'method': 'command/exec/terminate', 'params': {'processId': process_id}})
                response(3, time.monotonic() + 3)
                result['termination_acknowledged'] = True
            except (OSError, ValueError, EOFError, RuntimeError, TimeoutError):
                result['termination_acknowledged'] = False
        try: process.stdin.close()
        except OSError: pass
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=3)
        for reader in readers: reader.join(1)
        process.stdout.close(); process.stderr.close()
        if stderr: result['server_stderr'] = ''.join(stderr)[-2000:]
    return result
