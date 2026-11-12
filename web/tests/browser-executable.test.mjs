import assert from 'node:assert/strict'
import {test} from 'node:test'
import {browserCandidates, resolveBrowserExecutable} from './browser-executable.mjs'

const base = {env: {}, platform: 'darwin', home: '/home/test', bundled: '/bundled/chromium'}

test('explicit browser wins; invalid explicit configuration fails without fallback', () => {
  assert.equal(resolveBrowserExecutable({...base, env: {HARNESS_TEST_BROWSER: '/custom'}, available: () => true}), '/custom')
  assert.throws(() => resolveBrowserExecutable({...base, env: {HARNESS_TEST_BROWSER: '/missing'},
    available: file => file !== '/missing'}), /HARNESS_TEST_BROWSER.*\/missing/)
})

test('installed Playwright Chromium wins over system browsers', () => {
  assert.equal(resolveBrowserExecutable({...base, available: () => true}), base.bundled)
})

test('missing Playwright Chromium selects installed macOS browser', () => {
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  assert.equal(resolveBrowserExecutable({...base, available: file => file === chrome}), chrome)
  const userChrome = '/home/test/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
  assert.equal(resolveBrowserExecutable({...base, available: file => file === userChrome}), userChrome)
})

test('Windows discovery supports system and per-user browser directories', () => {
  const env = {PROGRAMFILES: 'C:\\Program Files', LOCALAPPDATA: 'C:\\Users\\test\\AppData\\Local'}
  const candidates = browserCandidates('win32', env, base.home)
  const edge = 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'
  assert.ok(candidates.includes(edge))
  assert.ok(candidates.includes('C:\\Users\\test\\AppData\\Local\\Google\\Chrome\\Application\\chrome.exe'))
  assert.equal(resolveBrowserExecutable({...base, platform: 'win32', env, available: file => file === edge}), edge)
})

test('Linux discovery searches PATH without executing shell commands', () => {
  const env = {PATH: '/custom/bin:/usr/bin'}
  assert.equal(resolveBrowserExecutable({...base, platform: 'linux', env,
    available: file => file === '/custom/bin/chromium'}), '/custom/bin/chromium')
})

test('missing all browsers gives actionable installation instructions', () => {
  assert.throws(() => resolveBrowserExecutable({...base, available: () => false}),
    /playwright-core install chromium.*HARNESS_TEST_BROWSER.*\/bundled\/chromium/)
})

test('real availability check rejects directories and nonexistent paths', () => {
  for (const file of [import.meta.dirname, '/nonexistent/harness-test-browser']) {
    assert.throws(() => resolveBrowserExecutable({env: {HARNESS_TEST_BROWSER: file}}), /not an executable file/)
  }
})
