import assert from 'node:assert/strict'
import {test} from 'node:test'
import {WebsitePermissions} from '../permissions.mjs'

const url = 'https://example.test/page'
const details = {requestingUrl: url}

function fixture(t) {
  const permissions = new WebsitePermissions()
  t.after(() => {for (const request of permissions.pending.values()) clearTimeout(request.timer)})
  return permissions
}

function grant(permissions, tab, capability) {
  permissions.request(tab, url, capability, () => {}, details)
  const request = [...permissions.pending.values()].find(request => request.tab === tab)
  permissions.answer(tab, url, request.id, true)
}

test('clearing a tab releases all owned state even if a permission callback throws', t => {
  const permissions = fixture(t)
  const log = t.mock.method(console, 'error', () => {})
  grant(permissions, 'first', 'clipboard-read')
  grant(permissions, 'other', 'clipboard-read')
  const answers = []
  permissions.request('first', url, 'notifications', () => {throw Error('callback failed')}, details)
  permissions.request('first', url, 'geolocation', allow => answers.push(allow), details)
  permissions.request('other', url, 'geolocation', () => {throw Error('wrong tab')}, details)
  const other = permissions.snapshot('other', url)
  assert.doesNotThrow(() => permissions.clear('first'))
  assert.deepEqual(permissions.snapshot('first', url), {origin: 'https://example.test', grants: [], requests: []})
  assert.deepEqual(answers, [false])
  assert.deepEqual(permissions.snapshot('other', url), other)
  assert.equal(log.mock.callCount(), 1)
})

test('clearing detaches requests and grants before invoking cancellation callbacks', t => {
  const permissions = fixture(t)
  grant(permissions, 'first', 'clipboard-read')
  const observed = []
  for (const capability of ['notifications', 'geolocation']) {
    permissions.request('first', url, capability, allow => {
      observed.push({allow, snapshot: permissions.snapshot('first', url)})
    }, details)
  }
  permissions.clear('first')
  assert.deepEqual(observed, [false, false].map(allow => ({allow,
    snapshot: {origin: 'https://example.test', grants: [], requests: []}})))
})

test('permission answers remain bound to the requesting tab and origin', t => {
  const permissions = fixture(t)
  const answers = []
  permissions.request('first', url, 'notifications', allow => answers.push(allow), details)
  const id = [...permissions.pending.keys()][0]
  assert.throws(() => permissions.answer('other', url, id, true), error => error.code === 'browser/permission-inactive')
  assert.throws(() => permissions.answer('first', 'https://other.test', id, true), error => error.code === 'browser/permission-inactive')
  assert.deepEqual(answers, [])
  permissions.answer('first', url, id, true)
  assert.deepEqual(answers, [true])
  assert.equal(permissions.check('first', url, url, 'notifications'), true)
  assert.equal(permissions.check('other', url, url, 'notifications'), false)
  assert.throws(() => permissions.answer('first', url, id, true), error => error.code === 'browser/permission-inactive')
})

test('malformed permission decisions do not consume or deny the pending request', t => {
  const permissions = fixture(t), answers = []
  permissions.request('first', url, 'notifications', allow => answers.push(allow), details)
  const before = permissions.snapshot('first', url), id = before.requests[0].id
  for (const value of [undefined, null, 'true', 'false', 0, 1, {}, []]) {
    assert.throws(() => permissions.answer('first', url, id, value), error => error.copy?.key === 'browser.desktop.permissionInvalid')
    assert.deepEqual(permissions.snapshot('first', url), before)
    assert.deepEqual(answers, [])
  }
  permissions.answer('first', url, id, false)
  assert.deepEqual(answers, [false])
  assert.equal(permissions.check('first', url, url, 'notifications'), false)
})
