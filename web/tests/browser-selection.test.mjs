import assert from 'node:assert/strict'
import {test} from 'node:test'
import {selectBrowserScenarios} from '../../scripts/browser_selection.mjs'

test('default discovers all scenarios and explicit selection preserves complete scenario execution', () => {
  const available = ['runtime-smoke.mjs', 'goal-mode-smoke.mjs']
  assert.deepEqual(selectBrowserScenarios(available, []), [...available].sort())
  assert.deepEqual(selectBrowserScenarios(available, ['runtime-smoke.mjs', 'runtime-smoke.mjs']), ['runtime-smoke.mjs'])
})

test('empty discovery, typos and paths outside the discovered suite fail', () => {
  assert.throws(() => selectBrowserScenarios([], []), /discovery failed/)
  for (const name of ['runtim-smoke.mjs', '../runtime-smoke.mjs', '--unknown']) {
    assert.throws(() => selectBrowserScenarios(['runtime-smoke.mjs'], [name]), /Unknown browser scenarios/)
  }
})

test('shards cover every discovered scenario exactly once in stable order', () => {
  const available = Array.from({length: 11}, (_, index) => `scenario-${index}-smoke.mjs`).reverse()
  const shards = Array.from({length: 4}, (_, index) => selectBrowserScenarios(available, [`--shard=${index + 1}/4`]))
  const combined = shards.flat()
  assert.equal(new Set(combined).size, combined.length)
  assert.deepEqual(combined.sort(), [...available].sort())
  for (let index = 0; index < shards.length; index++) {
    assert.deepEqual(shards[index], selectBrowserScenarios([...available].reverse(), [`--shard=${index + 1}/4`]))
    assert.ok(shards[index].length > 0)
  }
})

test('invalid, empty and mixed shard selections fail instead of reducing coverage', () => {
  const available = ['one-smoke.mjs', 'two-smoke.mjs']
  for (const requested of [
    ['--shard=0/2'], ['--shard=3/2'], ['--shard=1/3'], ['--shard=1/0'],
    ['--shard=1.5/2'], ['--shard=1/9007199254740992'], ['--shard=1/2', 'one-smoke.mjs'],
    ['--shard=1/2', '--shard=2/2'], ['--shard=1/2', '--unknown'],
  ]) assert.throws(() => selectBrowserScenarios(available, requested), /shard/)
  assert.throws(() => selectBrowserScenarios([], ['--shard=1/1']), /discovery failed/)
})
