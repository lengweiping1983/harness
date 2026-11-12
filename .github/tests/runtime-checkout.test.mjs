import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import test from 'node:test';

test('Windows autocrlf checkout preserves generator inputs and runtime bytes', () => {
  const root = mkdtempSync(join(tmpdir(), 'harness-checkout-'));
  const git = (...args) => execFileSync('git', args, { cwd: root, stdio: 'pipe' });
  try {
    git('init');
    git('config', 'core.autocrlf', 'true');
    git('config', 'user.name', 'Harness regression');
    git('config', 'user.email', 'regression@example.invalid');
    writeFileSync(join(root, '.gitattributes'), readFileSync(new URL('../../.gitattributes', import.meta.url)));
    const paths = ['model-runtime/licenses/pi-ai.txt', 'model-runtime/licenses/DeepSeek.txt',
      'harness/llm/runtime/LICENSE.pi-ai', 'harness/llm/runtime/bridge.mjs',
      'harness/llm/runtime/catalog.json', 'harness/llm/runtime/package.json',
      'harness/llm/runtime/THIRD-PARTY-NOTICES.txt'];
    const contents = Buffer.from('first line\nsecond line\n');
    for (const path of paths) {
      mkdirSync(dirname(join(root, path)), { recursive: true });
      writeFileSync(join(root, path), contents);
    }
    git('add', '.');
    git('commit', '-m', 'checkout fixture');
    for (const path of paths) rmSync(join(root, path));
    git('checkout', '--', ...paths);
    for (const path of paths) assert.deepEqual(readFileSync(join(root, path)), contents, path);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
