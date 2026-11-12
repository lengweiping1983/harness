import type { Block, ToolBlockState, RuntimeTerminal } from '@/types'

/** Terminal IDs never restart. Late observers cannot undo confirmed exit. */
export function terminalSnapshot(previous: RuntimeTerminal | undefined, incoming: RuntimeTerminal): RuntimeTerminal {
  if (previous?.status.kind === 'exited' && !previous.control && (incoming.status.kind === 'running' || incoming.control?.state === 'stopping')) return previous
  return incoming
}

const choices = new Map<string, string>()
const hidden = new Map<string, string[]>()
export function hiddenTerminalTabs(sessionId: string): string[] { return hidden.get(sessionId) ?? [] }
export function hideTerminalTab(sessionId: string, id: string): string[] {
  const ids = [...hiddenTerminalTabs(sessionId).filter(value => value !== id), id].slice(-128)
  hidden.delete(sessionId); hidden.set(sessionId, ids)
  if (hidden.size > 64) hidden.delete(hidden.keys().next().value!)
  return ids
}
export function showTerminalTab(sessionId: string, id: string): string[] {
  const ids = hiddenTerminalTabs(sessionId).filter(value => value !== id)
  if (hidden.has(sessionId)) hidden.set(sessionId, ids)
  return ids
}
/** Match BrowserTabs: prefer the right neighbour, then the previous tab. */
export function terminalChoiceAfterClose(tabs: string[], selected: string, closed: string): string {
  if (selected !== closed) return selected
  const index = tabs.indexOf(closed)
  return tabs[index + 1] ?? tabs[index - 1] ?? 'commands'
}
export function terminalChoice(sessionId: string): string { return choices.get(sessionId) ?? 'commands' }
export function rememberTerminalChoice(sessionId: string, choice: string): void {
  choices.delete(sessionId); choices.set(sessionId, choice)
  if (choices.size > 64) choices.delete(choices.keys().next().value!)
}

/** Keep every live command and a bounded tail of completed receipts. */
export function terminalCommands(blocks: Block[]): ToolBlockState[] {
  const commands = blocks.filter((block): block is ToolBlockState => block.kind === 'tool' && ['bash', 'pwsh', 'run_code', 'terminal_send'].includes(block.name))
  const recent = new Set(commands.slice(-20).map(block => block.id))
  return commands.filter(block => block.status === 'running' || recent.has(block.id))
}

export function terminalCommandText(block: ToolBlockState): string {
  const display = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
  if (block.status !== 'running') {
    if (typeof display.stdout === 'string' || typeof display.stderr === 'string') {
      return `${typeof display.stdout === 'string' ? display.stdout : ''}${typeof display.stderr === 'string' && display.stderr ? `\n[stderr]\n${display.stderr}` : ''}`
    }
    if (typeof block.output === 'string' && block.output) return block.output
  }
  return block.progress ? block.progress.stdout + (block.progress.stderr ? `\n[stderr]\n${block.progress.stderr}` : '') : ''
}

export function terminalCommandBody(block: ToolBlockState): string {
  const input = block.input && typeof block.input === 'object' ? block.input as Record<string, unknown> : {}
  return ['command', 'cmd', 'script', 'code', 'text'].map(key => input[key]).find((value): value is string => typeof value === 'string') ?? block.name
}
