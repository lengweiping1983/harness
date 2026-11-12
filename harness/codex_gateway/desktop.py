"""管理 Codex 桌面端的可回滚 Harness provider 配置。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .__main__ import desktop_config
from .quota import QuotaError


class DesktopIntegration:
    """以原始字节备份配置，关闭时按原样恢复。"""

    def __init__(self, home: Path, *, codex_home: Path | None = None, port: int = 18765, model: str = 'chatgpt/gpt-6.1-sol'):
        self.home = Path(home)
        self.codex_home = Path(codex_home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
        self.path = self.codex_home / 'config.toml'
        self.backup = self.home / 'codex-desktop-config.backup'
        self.port, self.model = port, model
        self.process = None

    def status(self):
        return {'enabled': self.backup.exists(), 'config_path': str(self.path), 'port': self.port}

    def set_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise QuotaError('ui.codexGateway.invalidRequest')
        if enabled:
            if not self.backup.exists():
                original = self.path.read_bytes() if self.path.exists() else b''
                self.backup.parent.mkdir(parents=True, exist_ok=True)
                self.backup.write_bytes(original)
                os.chmod(self.backup, 0o600)
            current = self.path.read_text(encoding='utf-8') if self.path.exists() else ''
            lines = current.splitlines()
            cleaned = []
            skipping = False
            for line in lines:
                stripped = line.strip()
                if stripped.startswith('[model_providers.harness-subscriptions]') or stripped.startswith('[model_providers.harness-subscriptions.auth]'):
                    skipping = True
                    continue
                if skipping and stripped.startswith('['):
                    skipping = False
                if not skipping:
                    if stripped.startswith('model =') or stripped.startswith('model_provider ='):
                        continue
                    cleaned.append(line)
            first_table = next((index for index, line in enumerate(cleaned) if line.strip().startswith('[')), len(cleaned))
            root = ['model = ' + __import__('json').dumps(self.model), 'model_provider = "harness-subscriptions"', '']
            content = '\n'.join(cleaned[:first_table] + root + cleaned[first_table:]).rstrip() + '\n\n' + desktop_config(self.home, self.port, self.model).split('\n', 2)[2]
            self.codex_home.mkdir(parents=True, exist_ok=True)
            self.path.write_text(content, encoding='utf-8')
            os.chmod(self.path, 0o600)
            if self.process is None or self.process.poll() is not None:
                self.process = subprocess.Popen([sys.executable, '-m', 'harness.codex_gateway', '--home', str(self.home), 'serve', '--port', str(self.port)], cwd=str(Path(__file__).resolve().parents[2]), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        elif self.backup.exists():
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            self.process = None
            self.codex_home.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(self.backup.read_bytes())
            self.backup.unlink()
        return self.status()
