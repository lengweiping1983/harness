import { t } from '@/i18n'
import type { SkillInfo } from '@/types'
import type { TriggerCandidate } from './trigger'

/**
 * 触发器的候选来源。
 *
 * 技能和会话来自已有目录；Composer 的文件来源通过当前会话的只读
 * files/search 获取。
 */

/** 命令 id。UI 负责把 id 落到具体动作，候选表本身不持有行为。 */
export type CommandId =
  | 'session.new'
  | 'model.select'
  | 'composer.clear'
  | 'mode.plan'
  | 'mode.goal'
  | 'agent.summon'
  | 'skill.invoke'
  | 'view.theme'
  | 'session.export'
  | 'view.tools'
  | 'view.todos'
  | 'view.context'
  | 'view.files'
  | 'view.changes'
  | 'view.runtime'
  | 'view.sessions'
  | 'help.shortcuts'

export interface CommandSpec extends TriggerCandidate {
  id: CommandId
  /** Parameter commands replace the active token, then keep the editor open. */
  acceptsInput?: boolean
}

/**
 * 内置命令表。
 *
 * 刻意保持短小：每一条都必须**真的能执行**。列一堆灰色点不动的命令，
 * 比不列更伤信任。
 */
export const COMMANDS: CommandSpec[] = [
  { id: 'session.new', kind: 'command', insert: '/new', title: '/new', get hint() { return t('ui.lib.triggerSources.58e21b8737cc') } },
  { id: 'model.select', kind: 'command', insert: '/model', title: '/model', get hint() { return t("ui.lib.triggerSources.7c8ef6ae8509") } },
  { id: 'composer.clear', kind: 'command', insert: '/clear', title: '/clear', get hint() { return t("ui.lib.triggerSources.1828cea62f43") } },
  { id: 'mode.goal', kind: 'command', insert: '/goal', title: '/goal', get hint() { return t("ui.lib.triggerSources.3ef3637db5b8") } },
  { id: 'mode.plan', kind: 'command', insert: '/plan', get hint() { return t("ui.lib.triggerSources.930b4f6056af") }, title: '/plan' },
  { id: 'agent.summon', kind: 'command', insert: '/agent', get hint() { return t('ui.lib.triggerSources.5bba954e5f4f') }, title: '/agent', acceptsInput: true },
  { id: 'skill.invoke', kind: 'command', insert: '/skill', get hint() { return t("ui.lib.triggerSources.a6a7d941a505") }, title: '/skill', acceptsInput: true },
  { id: 'view.theme', kind: 'command', insert: '/theme', title: '/theme', get hint() { return t("ui.lib.triggerSources.413ff97b6322") } },
  { id: 'session.export', kind: 'command', insert: '/export', title: '/export', get hint() { return t('ui.lib.triggerSources.75b7f622216e') } },
  { id: 'view.tools', kind: 'command', insert: '/tools', title: '/tools', get hint() { return t("ui.lib.triggerSources.b917dee78b7e") } },
  { id: 'view.todos', kind: 'command', insert: '/todos', title: '/todos', get hint() { return t("ui.lib.triggerSources.8922e7e5bf50") } },
  { id: 'view.context', kind: 'command', insert: '/context', title: '/context', get hint() { return t("ui.lib.triggerSources.fdc26757f998") } },
  { id: 'view.changes', kind: 'command', insert: '/changes', title: '/changes', get hint() { return t("ui.lib.triggerSources.d3d66a11b06a") } },
  { id: 'view.files', kind: 'command', insert: '/files', title: '/files', get hint() { return t("ui.lib.triggerSources.7448d2e81bd8") } },
  { id: 'view.runtime', kind: 'command', insert: '/ps', title: '/ps', get hint() { return t("ui.lib.triggerSources.24c6a9f127c1") } },
  { id: 'view.sessions', kind: 'command', insert: '/sessions', title: '/sessions', get hint() { return t('ui.lib.triggerSources.3c3cb708580c') } },
  { id: 'help.shortcuts', kind: 'command', insert: '/help', title: '/help', get hint() { return t("ui.lib.triggerSources.14bfc653ee31") } },
]

/** 技能引用。 */
export function skillCandidates(skills: SkillInfo[]): TriggerCandidate[] {
  return skills.filter((s) => s.user_invocable !== false).map((s) => ({
    kind: 'skill' as const,
    insert: '$' + s.name,
    title: s.name,
    hint: s.description ?? s.path,
  }))
}

/** `/` 的候选集。 */
export function commandCandidates(): CommandSpec[] {
  return COMMANDS
}
