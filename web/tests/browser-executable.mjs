import {accessSync, constants, statSync} from 'node:fs'
import {homedir} from 'node:os'
import path from 'node:path'
import {chromium} from 'playwright-core'

function isExecutable(file) {
  try {
    accessSync(file, constants.X_OK)
    return statSync(file).isFile()
  } catch {
    return false
  }
}

export function browserCandidates(platform, env, home) {
  if (platform === 'darwin') {
    return ['/Applications', path.posix.join(home, 'Applications')].flatMap(directory => [
      path.posix.join(directory, 'Google Chrome.app/Contents/MacOS/Google Chrome'),
      path.posix.join(directory, 'Microsoft Edge.app/Contents/MacOS/Microsoft Edge'),
      path.posix.join(directory, 'Chromium.app/Contents/MacOS/Chromium'),
    ])
  }
  if (platform === 'win32') {
    return [...new Set([env.PROGRAMFILES, env['PROGRAMFILES(X86)'], env.LOCALAPPDATA].filter(Boolean))]
      .flatMap(directory => [
        path.win32.join(directory, 'Google/Chrome/Application/chrome.exe'),
        path.win32.join(directory, 'Microsoft/Edge/Application/msedge.exe'),
        path.win32.join(directory, 'Chromium/Application/chrome.exe'),
      ])
  }
  return (env.PATH || '').split(path.posix.delimiter).filter(Boolean).flatMap(directory =>
    ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge']
      .map(name => path.posix.join(directory, name)))
}

export function resolveBrowserExecutable({
  env = process.env, platform = process.platform, home = homedir(),
  bundled = chromium.executablePath(), available = isExecutable,
} = {}) {
  if (env.HARNESS_TEST_BROWSER) {
    if (!available(env.HARNESS_TEST_BROWSER)) {
      throw new Error('HARNESS_TEST_BROWSER is not an executable file: ' + env.HARNESS_TEST_BROWSER)
    }
    return env.HARNESS_TEST_BROWSER
  }
  if (available(bundled)) return bundled
  const selected = browserCandidates(platform, env, home).find(available)
  if (selected) return selected
  throw new Error('No browser executable found. Run "cd web && npx playwright-core install chromium" '
    + 'or set HARNESS_TEST_BROWSER to a Chrome/Edge/Chromium executable. Expected Playwright Chromium: ' + bundled)
}
