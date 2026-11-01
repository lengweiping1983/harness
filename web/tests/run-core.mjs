import {readdirSync} from 'node:fs'
import {spawnSync} from 'node:child_process'
import path from 'node:path'
import {chromium} from 'playwright-core'
import {resolveBrowserExecutable} from './browser-executable.mjs'
import {selectBrowserScenarios} from '../../scripts/browser_selection.mjs'

const root = path.resolve(import.meta.dirname, '..')
const scripts = readdirSync(import.meta.dirname).filter(name => name.endsWith('-smoke.mjs')).sort()
const scenarios = selectBrowserScenarios(scripts, process.argv.slice(2)).map(name => [name, []])
const env = {...process.env}
if (scenarios.length) {
  try {
    env.HARNESS_TEST_BROWSER = resolveBrowserExecutable()
    const browser = await chromium.launch({executablePath: env.HARNESS_TEST_BROWSER, headless: true})
    try {
      console.log(`Core browser executable: ${env.HARNESS_TEST_BROWSER} (${browser.version()})`)
    } finally {
      await browser.close()
    }
  } catch (error) {
    console.error('Browser preflight failed:', error.message)
    process.exit(1)
  }
}
if (!scenarios.length) console.log('Browser framework ready: no scenarios registered')
const failed = []
for (const [name, args] of scenarios) {
  console.log(`\nCore browser: ${name} ${args.join(' ')}`)
  const result = spawnSync(process.execPath, [path.join(import.meta.dirname, name), ...args], {cwd: root, env, stdio: 'inherit'})
  if (result.error || result.status !== 0) {
    if (result.error) console.error(result.error)
    failed.push([name, ...args].join(' '))
  }
}
if (scenarios.length) console.log(`\nCore browser: ${scenarios.length - failed.length}/${scenarios.length} passed`)
if (failed.length) {
  console.error('Failed:', failed.join(', '))
  process.exitCode = 1
}
