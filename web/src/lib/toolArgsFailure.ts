import { t } from '@/i18n'
import type { ToolBlockState } from '@/types'

/** 参数拒绝必须有明确未执行证据，不从错误正文猜测副作用。 */
export function toolArgsFailure(block: ToolBlockState): boolean {
  if (block.status !== 'error' || !block.display || typeof block.display !== 'object') return false
  const receipt = block.display as Record<string, unknown>
  const details = receipt.details as Record<string, unknown> | undefined
  return receipt.code === 'tool/args' && details?.not_executed === true
}

export function toolArgsNote(block: ToolBlockState): string | null {
  if (!toolArgsFailure(block)) return null
  const input = block.input as Record<string, unknown> | undefined
  return t(block.name === 'bash' && typeof input?.timeout === 'number' && input.timeout > 900
    ? 'ui.toolArgs.bashTimeout' : 'ui.toolArgs.correct')
}

/** FS_EXISTS 在写入前或独占创建时拒绝，不把其它文件错误视作未写入。 */
export function fileWriteRejected(block: ToolBlockState): boolean {
  return block.status === 'error' && block.name === 'write' && !!block.display &&
    typeof block.display === 'object' && 'code' in block.display && block.display.code === 'FS_EXISTS'
}
