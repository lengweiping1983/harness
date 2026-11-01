/** 只识别有界的完整 JSON 文档；解析结果不用于展示，保留原始数值与重复键。 */
export function isJsonDocument(text: string): boolean {
  if (text.length > 1_000_000) return false
  const first = text.trimStart()[0]
  if (first !== '{' && first !== '[') return false
  try {
    const value: unknown = JSON.parse(text)
    return value !== null && typeof value === 'object'
  } catch { return false }
}
