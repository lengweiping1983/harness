/** 只判断 JSON 对象形状，字段约束由各协议消费者校验。 */
export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}
