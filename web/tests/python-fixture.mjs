import {fileURLToPath} from 'node:url'
import {developmentPython, pythonEnvironment} from '../../desktop/python-runtime.mjs'

const root = fileURLToPath(new URL('../../', import.meta.url))
// 测试夹具统一所属子解释器的默认编码，不逐场景改写参数。
Object.assign(process.env, pythonEnvironment())
export const fixturePython = developmentPython({root})
