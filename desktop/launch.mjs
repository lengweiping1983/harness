import {setupCopy} from './setup-copy.mjs'
import {fileURLToPath} from 'node:url'
import {prepareDesktop, runCommand} from './setup.mjs'
const allowed = new Set(['--prepare-only'])
try {
  if (process.argv.slice(2).length > 1 || process.argv.slice(2).some(argument => !allowed.has(argument))) throw Error(setupCopy()('desktop.setup.launchUsage'))
  const root = fileURLToPath(new URL('../', import.meta.url))
  const ready = await prepareDesktop({root})
  if (!process.argv.includes('--prepare-only')) {
    const environment = {...ready.env}
    delete environment.ELECTRON_RUN_AS_NODE
    const options = {cwd:fileURLToPath(new URL('./', import.meta.url)), env:environment}
    await runCommand(process.execPath, ['node_modules/electron/cli.js', '.'], options)
  }
} catch (error) {console.error('Harness Desktop: ' + error.message); process.exitCode = 1}
