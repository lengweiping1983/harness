/** 将现有任务交给技能或团队；切换调用时只替换最前面的调用前缀。 */
export function invocationTask(draft: string): string {
  const agent = /^\/agent\s+\S+(?:\s+|$)/.test(draft.trim())
  let task = draft.trim().replace(/^\/(?:agent|skill|runner)\s+\S+(?:\s+|$)/, '').trim()
  if (agent) while (/^--(?:worktrees|working-copy)(?:\s+|$)/.test(task)) task = task.replace(/^--(?:worktrees|working-copy)(?:\s+|$)/, '').trim()
  return task
}
export function invocationDraft(kind: 'agent' | 'skill' | 'runner', name: string, draft: string): string {
  const task = invocationTask(draft)
  return '/' + kind + ' ' + name + ' ' + task
}
