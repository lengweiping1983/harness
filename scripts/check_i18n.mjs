/** Validate the authoritative catalogs and generated browser views. */
import fs from 'node:fs'
import path from 'node:path'
import ts from '../web/node_modules/typescript/lib/typescript.js'
const root = path.resolve(import.meta.dirname, '..')
const read = file => JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(fs.readFileSync(path.join(root, file))))
const zh = read('harness/i18n/locales/zh-CN.json'), en = read('harness/i18n/locales/en.json')
const errors = []
const browserCatalogs = ['zh-CN', 'en'].map(language => read(`web/src/i18n/locales/${language}.json`))
const placeholders = text => [...text.matchAll(/(?<!\{)\{[A-Za-z_]\w*\}(?!\})|\{\{[A-Za-z_]\w*\}\}/g)].map(match => match[0]).sort().join('|')
if (Object.keys(zh).sort().join('|') !== Object.keys(en).sort().join('|')) errors.push('Catalog keys differ')
// 固定载荷键按设计不随界面语言改写：接续正文中英均保持“继续”，见 harness/mcp_server/companion.py。
const fixedPayloadKeys = new Set(['execution.companion.prompt'])
for (const [key, source] of Object.entries(zh)) {
  if (typeof en[key] !== 'string') { errors.push(`Missing English resource: ${key}`); continue }
  if (placeholders(source) !== placeholders(en[key])) errors.push(`Placeholder mismatch: ${key}`)
  if (!fixedPayloadKeys.has(key) && /\p{Script=Han}/u.test(en[key])) errors.push(`Untranslated English prose: ${key}`)
}
for (const [language, catalog] of [['zh-CN', zh], ['en', en]]) {
  const expected = Object.fromEntries(Object.entries(catalog).filter(([key]) => /^(ui\.|settings\.language\.|files\.open\.|browser\.)/.test(key)))
  if (JSON.stringify(expected) !== JSON.stringify(read(`web/src/i18n/locales/${language}.json`))) errors.push(`Stale ${language} browser catalog; run python scripts/generate_i18n.py`)
}
function files(dir) { return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
  ? ['__tests__', 'i18n', 'research'].includes(entry.name) ? [] : files(path.join(dir, entry.name))
  : /\.tsx?$/.test(entry.name) && !entry.name.includes('.generated.') ? [path.join(dir, entry.name)] : []) }
for (const file of files(path.join(root, 'web/src'))) {
  const source = new TextDecoder('utf-8', {fatal:true}).decode(fs.readFileSync(file)), tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true)
  function visit(node) {
    // Comments, regular expressions, type declarations, and protocol comparisons are excluded.
    if (ts.isTypeNode(node) || ts.isRegularExpressionLiteral(node)) return
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
      const left = node.left.getText(tree), right = node.right.getText(tree)
      if (/\bt\s*\(/.test(left) && /\.message\b/.test(right) || /\bt\s*\(/.test(right) && /\.message\b/.test(left)) {
        errors.push(`Concatenated error prose: ${path.relative(root, file)}:${tree.getLineAndCharacterOfPosition(node.pos).line + 1}; use a complete translation template or errorWithGuidance`)
      }
    }
    if (ts.isCallExpression(node) && node.expression.getText(tree) === 't' && node.arguments.length) {
      function checkKey(argument) {
        if (ts.isParenthesizedExpression(argument)) return checkKey(argument.expression)
        if (ts.isConditionalExpression(argument)) { checkKey(argument.whenTrue); checkKey(argument.whenFalse); return }
        if (!ts.isStringLiteral(argument) && !ts.isNoSubstitutionTemplateLiteral(argument)) return
        const key = argument.text
        if (browserCatalogs.some(catalog => !Object.hasOwn(catalog, key))) errors.push(`Unknown browser translation key ${key} in ${path.relative(root, file)}`)
      }
      checkKey(node.arguments[0])
    }
    if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isJsxText(node)) && /\p{Script=Han}/u.test(node.text)) {
      const parent = node.parent
      const protocol = ts.isPropertyAssignment(parent) && parent.name === node || ts.isBinaryExpression(parent) && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken].includes(parent.operatorToken.kind)
      // The language menu deliberately names Chinese in its native language.
      const languageName = path.basename(file) === 'SettingsDialog.tsx' && node.text.trim() === '中文'
      if (!protocol && !languageName) errors.push(`Unregistered product text: ${path.relative(root, file)}:${tree.getLineAndCharacterOfPosition(node.pos).line + 1}`)
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
}
if (errors.length) { console.error(errors.join('\n')); process.exitCode = 1 } else console.log(`Bilingual resources verified: ${Object.keys(zh).length} keys; original content and comments excluded.`)
