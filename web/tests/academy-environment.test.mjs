import assert from 'node:assert/strict'
import {test} from 'node:test'
import {requireAcademySandbox} from './academy-environment.mjs'
import {isInfrastructureError} from './browser-runner.mjs'

test('an explicit unavailable sandbox blocks execution as infrastructure without accepting malformed project evidence', () => {
  for (const sandbox of ['seatbelt', 'bubblewrap']) requireAcademySandbox({sandbox})
  assert.throws(() => requireAcademySandbox({sandbox: 'unavailable'}), error => isInfrastructureError({infrastructure: error.browserInfrastructure}))
  for (const project of [null, {}, {sandbox: null}, {sandbox: 'windows'}, {sandbox: true}]) {
    assert.throws(() => requireAcademySandbox(project), error => error instanceof assert.AssertionError && !error.browserInfrastructure)
  }
})
