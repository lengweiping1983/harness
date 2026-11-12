/** Windows 在测试进程启动前选择轮询，避免原生监听触发 libuv 断言。 */
import {spawnSync} from 'node:child_process'
import {fileURLToPath} from 'node:url'

const cwd = fileURLToPath(new URL('../web/', import.meta.url))
const env = {...process.env}
if (process.platform === 'win32') env.CHOKIDAR_USEPOLLING = 'true'
const result = spawnSync(process.execPath, ['node_modules/vitest/vitest.mjs', 'run'], {cwd, env, stdio: 'inherit'})
if (result.error) console.error(result.error)
process.exitCode = result.status ?? 1
