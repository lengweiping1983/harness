/** 气泡隐藏输入模式前缀；原始消息仍用于执行、编辑及回声匹配。 */
export function userMessageText(text: string): string {
  return text.replace(/^\s*\/(?:goal|plan)\s+([\s\S]+)$/, '$1')
}
