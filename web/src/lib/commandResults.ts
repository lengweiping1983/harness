import { t } from '@/i18n'
import type { ToolBlockState } from '@/types'
import { toolArgsFailure } from './toolArgsFailure'

const commandNames = new Set(['bash', 'pwsh', 'run_code', 'terminal_send', 'exec_command', 'shell'])

/** 仅使用前台命令的结构化回执判断失败，不解析输出正文或后台启动结果。 */
export function commandFailed(block: ToolBlockState): boolean {
  if (!commandNames.has(block.name.toLowerCase()) || block.status === 'running') return false
  if (toolArgsFailure(block)) return false
  const data = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
  if (data.not_executed || data.steering_deferred || data.interrupted || data.background) return false
  if (block.status === 'error') return true
  if (block.name.toLowerCase() === 'terminal_send') return false
  return data.execution_state === 'not-started' || data.timed_out === true ||
    (typeof data.exit_code === 'number' && Number.isInteger(data.exit_code) && data.exit_code !== 0)
}

export function terminalWaitNote(reason: unknown): string {
  switch (reason) {
    case 'stdin_read': return t('ui.lib.commandResults.88fa796f1183')
    case 'inferred_idle': return t('ui.lib.commandResults.84dbc05a5258')
    case 'timeout': return t('ui.lib.commandResults.11c0c3f07e95')
    case 'session_exit': return t('ui.lib.commandResults.b6e712c3287b')
    case 'cancelled': return t('ui.lib.commandResults.4c911b2b45c8')
    default: return t('ui.lib.commandResults.3153e9aea0fb')
  }
}

/** Read receipts from structured execution evidence, never from command output. */
export function commandResult(block: ToolBlockState) {
  const data = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
  if (!commandNames.has(block.name.toLowerCase())) return { label: null, note: null, exitCode: null }
  if (toolArgsFailure(block)) return { label: t('ui.toolArgs.rejected'), note: null, exitCode: null }
  if (block.status === 'running' || data.not_executed || data.steering_deferred || data.interrupted) return { label: null, note: null, exitCode: null }
  if (data.background) return { label: block.status === 'error' ? null : data.promoted ? t("ui.lib.commandResults.e9dde79b523d") : t("ui.lib.commandResults.5b351ec38480"), note: null, exitCode: null }
  if (block.name.toLowerCase() === 'terminal_send') {
    if (block.status === 'error') return { label: null, note: null, exitCode: null }
    const wait = data.terminal_wait && typeof data.terminal_wait === 'object' ? data.terminal_wait as Record<string, unknown> : {}
    const note = terminalWaitNote(wait.reason)
    return { label: t("ui.lib.commandResults.8594f46ca513"), note, exitCode: null }
  }
  if (data.execution_state === 'not-started') return { label: t("ui.lib.commandResults.14f05d18d39e"), note: null, exitCode: null }
  if (data.timed_out) return { label: t("ui.lib.commandResults.d68cf15ad418"), note: null, exitCode: null }
  const exitCode = typeof data.exit_code === 'number' && Number.isInteger(data.exit_code) ? data.exit_code : null
  if (exitCode === 0 && Array.isArray(data.encoding_errors) && data.encoding_errors.length) return {label:t('ui.encoding.commandIncomplete'), note:null, exitCode}
  return { label: exitCode !== null && exitCode !== 0 ? t("ui.lib.commandResults.6eb1141858cf") : null, note: null, exitCode }
}
