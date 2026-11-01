export type ComposerMode = 'normal' | 'plan' | 'goal'

/** 普通草稿不能遮蔽会话限制；目标与计划选择仍互斥。 */
export function resolveComposerMode(selected: ComposerMode | undefined, planMode: boolean): ComposerMode {
  return selected && selected !== 'normal' ? selected : planMode ? 'plan' : 'normal'
}

/** 裸命令只选择输入模式；有任务正文的命令保留显式调用语义。 */
export function modeOnlyCommand(text: string): Exclude<ComposerMode, 'normal'> | null {
  return text.trim() === '/plan' ? 'plan' : text.trim() === '/goal' ? 'goal' : null
}

/** 模式仅在用户发送实际正文时进入执行链路，不改写其他显式命令。 */
export function modeTaskText(text: string, mode: ComposerMode, hasGoal = false, planMode = false): string {
  if (text.trimStart().startsWith('/') || mode === 'normal' || mode === 'goal' && hasGoal || mode === 'plan' && planMode) return text
  return `/${mode} ${text}`
}
