import fs from 'node:fs'
import path from 'node:path'
import postcss from '../web/node_modules/postcss/lib/postcss.mjs'
import tailwindcss from '../web/node_modules/tailwindcss/lib/index.js'
import config from '../web/tailwind.config.js'
import {cssFontErrors} from './ui_css_rules.mjs'
import {classRuleErrors} from './ui_class_rules.mjs'

const root = path.resolve(import.meta.dirname, '../web/src')
const errors = []
function walk(dir) {
  return fs.readdirSync(dir, {withFileTypes: true}).flatMap(entry => entry.isDirectory()
    ? ['__tests__', 'i18n', 'research'].includes(entry.name) ? [] : walk(path.join(dir, entry.name))
    : /\.(tsx?|css)$/.test(entry.name) ? [path.join(dir, entry.name)] : [])
}
for (const file of walk(root)) {
  // Windows 下 path.relative 返回反斜杠，统一为正斜杠后再做规则判断与展示。
  const relative = path.relative(root, file).split(path.sep).join('/')
  const source = new TextDecoder('utf-8', {fatal:true}).decode(fs.readFileSync(file))
  if (file.endsWith('.css')) errors.push(...cssFontErrors(source, relative))
  errors.push(...classRuleErrors(source, relative))
}
const samples = ['bg-brand/[0.12]', 'bg-ok/[0.12]', 'bg-warn/[0.12]', 'bg-danger/[0.12]', 'text-compact', 'hover:bg-raised', 'rounded-composer', 'leading-reading', 'leading-code', 'max-h-dialog', 'z-overlay', 'grid-cols-settings', 'shadow-control']
const css = (await postcss([tailwindcss({...config, content: [{raw: samples.join(' ')}]})]).process('@tailwind utilities;', {from: undefined})).css
for (const token of ['--c-brand', '--c-ok', '--c-warn', '--c-danger', '--text-compact', '--c-raised', '.rounded-composer', '.leading-reading', '.leading-code', '.max-h-dialog', '.z-overlay', '.grid-cols-settings', '.shadow-control']) {
  if (!css.includes(token)) errors.push('Missing generated style: ' + token)
}
if (errors.length) {console.error(errors.join('\n')); process.exitCode = 1}
else console.log('UI rules verified: typography scale and state backgrounds compile.')
