import { invocationDraft, invocationTask } from './invocationDraft'

export interface ComposerSkill { name: string; description?: string; enabled?: boolean; user_invocable?: boolean; interface?: {display_name?: string; short_description?: string; default_prompt?: string} }
export function skillDisplayName(skill: ComposerSkill): string { return skill.interface?.display_name || skill.name }
export function skillDescription(skill: ComposerSkill): string { return skill.interface?.short_description || skill.description || '' }
export function filterComposerSkills(skills: ComposerSkill[], query: string): ComposerSkill[] {
  const needle = query.trim().toLocaleLowerCase()
  return skills.filter(skill => skill.enabled !== false && skill.user_invocable !== false
    && `${skill.name} ${skillDisplayName(skill)} ${skillDescription(skill)} ${skill.description ?? ''}`.toLocaleLowerCase().includes(needle))
}
export function selectedSkillName(draft: string): string | null {
  return draft.trimStart().match(/^\/skill\s+(\S+)(?:\s|$)/)?.[1] ?? null
}
export function selectComposerSkill(skill: ComposerSkill, draft: string): string {
  const task = draft.trim() === '/skill' ? '' : invocationTask(draft)
  let initial = skill.interface?.default_prompt?.trim() ?? ''
  // 显式前缀已经选择技能，自身引用用普通名称展示，避免重复加载或移除后残留调用。
  const names = [...new Set([skill.name, skill.name.split(':').at(-1)!])]
    .map(name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  initial = initial.replace(new RegExp('\\$(' + names.join('|') + ')(?![A-Za-z0-9:-])', 'g'), '$1')
  return invocationDraft('skill', skill.name, task || initial)
}
export function removeComposerSkill(draft: string): string {
  return selectedSkillName(draft) ? invocationTask(draft) : draft
}
