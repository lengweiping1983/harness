export interface TeamAuthoringMember {
  name: string
  skills?: string[] | null
  display_name?: string | null
  access?: 'inherit' | 'read'
  execution?: 'inherit' | 'model'
  role: string
  agent?: string | null
  prompt?: string
  model?: string | null
  depends_on?: string[]
  runner?: 'codex' | 'claude' | 'opencode' | null
}

/** 切换主理人时同步成员身份与依赖，始终只保留一个主理人。 */
export function updateTeamMember<T extends TeamAuthoringMember>(rows: T[], index: number, patch: Partial<T>, canLead: (row: T) => boolean = row => !row.runner): T[] {
  const original = rows[index]
  if (!original) return rows
  const promoted = patch.role === 'lead'
  const result = rows.map((row, position) => {
    const updated = position === index ? { ...row, ...patch, ...(promoted ? { runner: null } : {}) }
      : promoted && row.role === 'lead' ? { ...row, role: 'member' } : { ...row }
    updated.depends_on = updated.role === 'lead' ? [] : (updated.depends_on ?? []).flatMap(name =>
      name !== original.name ? [name] : promoted ? [] : [patch.name ?? name])
    return updated as T
  })
  // 取消当前主理人的身份时优先选择模型成员，外部执行器不能担任主理人。
  if (!result.some(row => row.role === 'lead')) {
    const successor = result.findIndex(row => canLead(row))
    if (successor >= 0) {
      result[successor] = { ...result[successor], role: 'lead', depends_on: [] }
      for (const row of result) row.depends_on = (row.depends_on ?? []).filter(name => name !== result[successor].name)
    }
  }
  return result
}

/** 删除成员后清理依赖，并为剩余模型成员恢复主理人。 */
export function removeTeamMember<T extends TeamAuthoringMember>(rows: T[], index: number, canLead: (row: T) => boolean = row => !row.runner): T[] {
  const removed = rows[index]
  if (!removed) return rows
  const result = rows.filter((_, position) => position !== index).map(row => ({
    ...row, depends_on: (row.depends_on ?? []).filter(name => name !== removed.name),
  }))
  if (!result.some(row => row.role === 'lead')) {
    const successor = result.findIndex(row => canLead(row))
    if (successor >= 0) {
      result[successor] = { ...result[successor], role: 'lead', depends_on: [] }
      for (const row of result) row.depends_on = (row.depends_on ?? []).filter(name => name !== result[successor].name)
    }
  }
  return result
}


/** 仅在完整且类型安全时将原始成员 JSON 交给表单，输入原文由编辑器保留。 */
export function readTeamMembers(text: string): TeamAuthoringMember[] | null {
  try {
    const value: unknown = JSON.parse(text)
    if (!Array.isArray(value) || value.some(row => {
      if (!row || typeof row !== 'object' || typeof row.name !== 'string' || typeof row.role !== 'string') return true
      if (['display_name', 'agent', 'model', 'runner'].some(key => row[key] != null && typeof row[key] !== 'string')) return true
      if (row.skills != null && (!Array.isArray(row.skills) || row.skills.some((name: unknown) => typeof name !== 'string'))) return true
      if (row.prompt !== undefined && typeof row.prompt !== 'string') return true
      if (row.runner != null && !['codex','claude','opencode'].includes(row.runner)) return true
      if (row.access !== undefined && !['inherit','read'].includes(row.access)) return true
      if (row.execution !== undefined && !['inherit','model'].includes(row.execution)) return true
      return row.depends_on !== undefined && (!Array.isArray(row.depends_on) || row.depends_on.some((name: unknown) => typeof name !== 'string'))
    })) return null
    return value
  } catch { return null }
}

/** 在保存前指出可修复的成员身份和依赖问题；后端仍负责最终校验。 */
export function teamMemberIssue(rows: TeamAuthoringMember[], canLead: (row: TeamAuthoringMember) => boolean): string | null {
  const names = rows.map(row => row.name.trim())
  if (names.some(name => !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name))) return 'memberIdentifier'
  if (new Set(names).size !== names.length) return 'duplicateMember'
  if (rows.some(row => !['lead','member'].includes(row.role))) return 'memberRole'
  if (rows.filter(row => row.role === 'lead').length !== 1) return 'leaderCount'
  if (rows.some(row => row.role === 'lead' && !canLead(row))) return 'externalLead'
  if (rows.some(row => (row.display_name?.length ?? 0) > 120)) return 'memberDisplayLimit'
  const byName = new Map(rows.map(row => [row.name.trim(), row]))
  for (const row of rows) {
    const deps = (row.depends_on ?? []).map(name => name.trim())
    if ((row.role === 'lead' && deps.length) || new Set(deps).size !== deps.length || deps.some(name => name === row.name.trim() || !byName.has(name) || byName.get(name)?.role === 'lead')) return 'invalidDependency'
  }
  const active = new Set<string>(), done = new Set<string>()
  const cycle = (name: string): boolean => {
    if (active.has(name)) return true
    if (done.has(name)) return false
    active.add(name)
    if ((byName.get(name)?.depends_on ?? []).map(dependency => dependency.trim()).some(cycle)) return true
    active.delete(name); done.add(name); return false
  }
  return names.some(cycle) ? 'dependencyCycle' : null
}

/** 外部成员首次入队时补齐模型主理人，重复添加使用独立稳定标识。 */
export function addExternalTeamMember(rows: TeamAuthoringMember[], runner: NonNullable<TeamAuthoringMember['runner']>, prompts: { lead: string; member: string; leadName: string }): TeamAuthoringMember[] {
  if (rows.length >= 8) return rows
  const next = rows.length ? [...rows] : [{name: 'coordinator', display_name: prompts.leadName, role: 'lead', execution: 'model' as const, prompt: prompts.lead}]
  let name = runner + '-member'
  let suffix = 2
  while (next.some(row => row.name === name)) name = runner + '-member-' + suffix++
  return [...next, {name, display_name: runner === 'claude' ? 'Claude Code' : runner === 'codex' ? 'Codex' : 'OpenCode', role: 'member', runner, prompt: prompts.member}]
}

/** 按运行时优先级展示成员的生效模型，空字符串与未配置采用相同继承规则。 */
export function resolveMemberRuntime(row: TeamAuthoringMember, reference?: {model?: string | null; runner?: TeamAuthoringMember['runner']}, teamModel = ''): {runner: TeamAuthoringMember['runner']; model: string; source: 'member' | 'agent' | 'team' | 'conversation'} {
  if (row.runner) return {runner: row.runner, model: '', source: 'member'}
  if (row.model) return {runner: null, model: row.model, source: 'member'}
  if (row.execution === 'model') return {runner: null, model: '', source: 'conversation'}
  if (reference?.runner) return {runner: reference.runner, model: '', source: 'agent'}
  if (reference?.model) return {runner: null, model: reference.model, source: 'agent'}
  if (teamModel) return {runner: null, model: teamModel, source: 'team'}
  return {runner: null, model: '', source: 'conversation'}
}

/** 标识修改先完整校验，再由表单一次提交，避免中间重名破坏既有引用。 */
export function memberRenameIssue(rows: TeamAuthoringMember[], index: number, name: string): 'memberIdentifier' | 'duplicateMember' | null {
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) return 'memberIdentifier'
  return rows.some((row, position) => position !== index && row.name === name) ? 'duplicateMember' : null
}

/** 新成员默认沿用 Agent 标识，团队内重名时追加数字后缀。 */
export function defaultTeamMemberName(rows: TeamAuthoringMember[], agent: string): string {
  if (!rows.some(row => row.name === agent)) return agent
  let suffix = 2
  let name = ''
  do {
    const ending = '-' + suffix++
    name = agent.slice(0, 64 - ending.length).replace(/-+$/, '') + ending
  } while (rows.some(row => row.name === name))
  return name
}
