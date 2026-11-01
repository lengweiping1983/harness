/** 检查共享排版与布局令牌；颜色透明度及内容属性不机械禁止。 */
export function classRuleErrors(source, relative) {
  const errors = []
  if (/\b(?:bg|text|border|ring)-hover\b/.test(source)) errors.push(relative + ': undefined hover theme color')
  const utilities = /\b(?:max-[hw]|min-[hw]|[hw]|size|rounded(?:-[trblse]{1,2})?|leading|tracking|z|grid-(?:cols|rows)|shadow|scale(?:-[xy])?|backdrop-blur|border-[trblse]|gap(?:-[xy])?|[pm][xytrblse]?|inset(?:-[xy])?|top|right|bottom|left|start|end|translate-[xy]|transition|accent)-\[[^\]\r\n]+\]/g
  for (const match of source.matchAll(utilities)) errors.push(relative + ': use a shared semantic utility: ' + match[0])
  if (/\btext-\[(?:length:)?[\d.]+(?:rem|px)\]/.test(source)
    || /\[font-size:[\d.]+(?:rem|px)\]/i.test(source)) errors.push(relative + ': use the shared text scale')
  if (/\b(?:bg|text|border|ring)-[\w-]+\/12\b/.test(source)) errors.push(relative + ': opacity 12 needs explicit arbitrary syntax')
  return errors
}
