import { t } from '@/i18n'
import type { AgentRow } from './extensionCatalog'
import type { TeamAuthoringMember } from './teamAuthoring'

/** 模板只保存资源身份；点击时解析当前语言，随后按用户草稿原文编辑。 */
export const TEAM_TEMPLATES = [
  { id: 'delivery', roles: ['planner', 'builder', 'reviewer'], writes: true },
  { id: 'security', roles: ['surface', 'analyst', 'challenger'], writes: false },
  { id: 'product', roles: ['discovery', 'designer', 'critic'], writes: false },
  { id: 'performance', roles: ['measurement', 'investigator', 'skeptic'], writes: false },
  { id: 'research', roles: ['researcher', 'counterpart', 'verifier'], writes: false },
] as const

export type TeamTemplateId = typeof TEAM_TEMPLATES[number]['id']
export const templateKey = (id: TeamTemplateId, field: string) => `ui.teamTemplates.${id}.${field}`

/** 内联成员避免安装顺序依赖；权限和依赖仍由现有宿主执行。 */
export function teamTemplate(id: TeamTemplateId, agents: readonly Pick<AgentRow, 'name'>[] = []): AgentRow & { skills: string[]; members: TeamAuthoringMember[] } {
  const template = TEAM_TEMPLATES.find(row => row.id === id)!
  const occupied = new Set(agents.map(agent => agent.name))
  const baseName = 'team-' + id
  let name = baseName
  // 编辑器给模板副本追加 -custom；预选完整命名空间中尚未占用的最终标识。
  for (let suffix = 2; occupied.has(name + '-custom'); suffix++) name = baseName + '-' + suffix
  const key = (field: string) => templateKey(id, field)
  const members: TeamAuthoringMember[] = [{
    name: 'coordinator', role: 'lead', access: 'read', execution: 'model',
    display_name: t('ui.teamTemplates.coordinator'), prompt: t(key('lead')),
  }, ...template.roles.map((name, index): TeamAuthoringMember => ({
    name, role: 'member', execution: 'model',
    display_name: t(key(name + '.name')), prompt: t(key(name + '.prompt')),
    access: template.writes && index === 1 ? 'inherit' : 'read',
    ...(template.writes && index > 0 ? { depends_on: [template.roles[index - 1]] } : {}),
  }))]
  return {
    name, agent_type: 'team', enabled: true, skills: [], model: null,
    display_name: t(key('name')), description: t(key('description')),
    metadata: { display_name: t(key('name')) }, members,
    system_prompt: t('ui.teamTemplates.protocol') + '\n\n' + t(key('workflow')),
  }
}
