import { hasTranslation, t } from '@/i18n'
import type { ToolBlockState } from '@/types'

/** 仅对明确未写入的待办回执提供说明；未知错误保留通用详情。 */
export function todoFailure(block: ToolBlockState): { reason: string; recovery: string } | null {
  if (block.status !== 'error' || !/^todo_(write|update|bind|read)$/.test(block.name)) return null
  let value: unknown = block.display
  if (!value && typeof block.output === 'string') {
    try { value = JSON.parse(block.output) } catch { return null }
  }
  if (!value || typeof value !== 'object') return null
  const receipt = value as Record<string, unknown>
  if (receipt.updated !== false || typeof receipt.code !== 'string') return null
  const key = `ui.todoFailure.reason.${receipt.code}`
  if (!hasTranslation(key)) return null
  return { reason: t(key), recovery: t(receipt.code === 'PLAN_NOT_APPROVED' ? 'ui.todoFailure.approval' : 'ui.todoFailure.recovery') }
}
