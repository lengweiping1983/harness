import postcss from '../web/node_modules/postcss/lib/postcss.mjs'

/** 按声明读取字号，不让合法CSS空白、注释或大小写绕过共享档位。 */
export function cssFontErrors(source, relative) {
  const errors = []
  postcss.parse(source, {from: relative}).walkDecls(declaration => {
    if (declaration.prop.toLowerCase() !== 'font-size') return
    const value = declaration.value.replace(/\/\*[\s\S]*?\*\//g, '').trim()
    const match = /^([\d.]+)(rem|px)$/i.exec(value)
    if (!match) return
    if (relative === 'components/chat/screenshot.css' && declaration.parent.type === 'rule'
      && declaration.parent.selector.trim() === '.screenshot-size') return
    const pixels = Number(match[1]) * (match[2].toLowerCase() === 'rem' ? 16 : 1)
    if (![12, 13, 14, 16, 20, 28].includes(pixels)) errors.push(relative + ': CSS font size must use the shared text scale: ' + declaration.toString())
  })
  return errors
}
