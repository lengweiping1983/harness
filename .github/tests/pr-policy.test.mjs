import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../workflows/pr-policy.yml', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const script = source.split('          script: |\n')[1].split('\n').map(line => line.slice(12)).join('\n');
const run = new (Object.getPrototypeOf(async function () {}).constructor)('context', 'core', 'github', script);
const valid = 'Closes #123\n\n## 变更与验收\n复现问题后修复行为，并提供可核验依据。\n\n## 验证证据\n执行完整回归，结果与当前输入一致。\n\n## 风险与回退\n通过回退提交恢复行为，不修改用户数据。';
async function check(body, { login = 'contributor', issue = {}, fail = false } = {}) {
  const errors = [];
  const calls = [];
  await run({ repo: { owner: 'owner', repo: 'harness' }, payload: { pull_request: { body, user: { login } } } },
    { info() {}, setFailed(message) { errors.push(message); } },
    { rest: { issues: { async get(args) { calls.push(args); if (fail) throw new Error('API unavailable'); return { data: issue }; } } } });
  return { errors, calls };
}
test('valid evidence resolves an issue in the current repository', async () => {
  const result = await check(valid);
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.calls, [{ owner: 'owner', repo: 'harness', issue_number: 123 }]);
});
test('missing and commented issue links fail', async () => {
  for (const body of [null, valid.replace('Closes #123', '<!-- Closes #123 -->')]) {
    const result = await check(body);
    assert.equal(result.errors.length, 1);
    assert.equal(result.calls.length, 0);
  }
});
test('a PR number cannot stand in for an issue', async () => {
  assert.match((await check(valid, { issue: { pull_request: {} } })).errors[0], /是 PR/);
});
test('missing evidence sections fail', async () => {
  assert.equal((await check('Fixes #123')).errors.length, 3);
});
test('API uncertainty fails instead of accepting an unverified issue', async () => {
  await assert.rejects(check(valid, { fail: true }), /API unavailable/);
});
test('Dependabot uses generated metadata without exempting other bots', async () => {
  assert.deepEqual((await check(null, { login: 'dependabot[bot]' })).errors, []);
  assert.equal((await check(null, { login: 'other[bot]' })).errors.length, 1);
});
test('Dependabot PRs skip the whole job so no runner starts', () => {
  const jobIf = source.split('\n').find(line => line.trim().startsWith('if:'));
  assert.equal(jobIf?.trim(), "if: github.actor != 'dependabot[bot]'");
});
test('issue references are deduplicated and untrusted text stays data', async () => {
  const result = await check(valid + '\nFixes #123\n${process.exit(1)}');
  assert.equal(result.calls.length, 1);
  assert.deepEqual(result.errors, []);
});
