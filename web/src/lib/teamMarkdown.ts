import { scanFences } from './streaming'

/** 兼容顶层中文二至六级标题，保护代码围栏、缩进内容和单井号话题标签。 */
export function normalizeTeamMarkdown(text: string): string {
  const heading = (part: string) => part.replace(/^(#{2,6})(?=[\u3400-\u9fff])/gm, '$1 ')
  let cursor = 0
  let result = ''
  for (const fence of scanFences(text)) {
    result += heading(text.slice(cursor, fence.start)) + text.slice(fence.start, fence.end)
    cursor = fence.end
  }
  return result + heading(text.slice(cursor))
}
