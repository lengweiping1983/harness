/** 分页后不足五项的尾数随当前批次一起展示。 */
export function listVisibleCount(total: number, limit: number): number {
  return total - limit < 5 ? total : limit
}
