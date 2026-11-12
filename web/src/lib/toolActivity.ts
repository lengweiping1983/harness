import { t } from '@/i18n'
import type { ToolBlockState } from '@/types'
import { commandResult } from './commandResults'
import { toolArgsFailure, fileWriteRejected } from './toolArgsFailure'

export type ActivityKind = 'read' | 'edit' | 'search' | 'image' | 'command' | 'web' | 'agent' | 'plan' | 'skill' | 'tool'
export interface ToolActivity { kind: ActivityKind; label: string; detail: string; files: string[] }

/** Classification never copies command bodies, file arrays or tool output. */
export function toolActivityKind(block: Pick<ToolBlockState, 'name' | 'input'>): ActivityKind {
  const name = block.name.toLowerCase()
  if (/^(read_image|read_images|view_image)$/.test(name)) return 'image'
  if (/^(read|read_file|cat|view_file)$/.test(name)) return 'read'
  if (/^(write|edit|apply_patch|write_file|edit_file)$/.test(name)) return 'edit'
  if (/^(grep|glob|find|search|list_dir|ls|session_search|session_event_search|team_history)$/.test(name)) return 'search'
  if (/^(bash|pwsh|run_code|exec_command|shell|terminal_send)$/.test(name)) return 'command'
  if (/^(web_search|web_fetch|browser)$/.test(name)) return 'web'
  if (/^(spawn_agent|followup_task|list_agents|wait_agent|wait_agents|send_message|interrupt_agent)$/.test(name)) return 'agent'
  if (/^(todo_write|todo_update|todo_bind|exit_plan_mode|create_goal|get_goal|update_goal)$/.test(name)) return 'plan'
  if (name === 'skill') return 'skill'
  return 'tool'
}

/** 面向人读的活动摘要；只陈述工具类型和实际状态，不推断业务写入成功。 */
export function toolActivity(block: ToolBlockState): ToolActivity {
  const input = block.input && typeof block.input === 'object' ? block.input as Record<string, unknown> : {}
  const files = [...new Set(['path', 'file_path', 'filePath', 'target_file', 'filename', 'file', 'paths'].flatMap((key) => {
    const value = input[key]
    return typeof value === 'string' ? [value] : Array.isArray(value) ? value.filter((path): path is string => typeof path === 'string') : []
  }).filter((path) => path.trim()))]
  const firstText = (...keys: string[]) => keys.map((key) => input[key]).find((value): value is string => typeof value === 'string' && Boolean(value.trim())) ?? ''
  const name = block.name.toLowerCase()
  const running = block.status === 'running'
  const kind = toolActivityKind(block)
  let action = t("ui.lib.toolActivity.5537e40e1bdf", {p0: (block.name)})
  let detail = ''
  if (name === 'team_history') { action = t('ui.team.history.activity'); detail = firstText('query') || (typeof input.message_seq === 'number' ? t('ui.team.history.message', { seq: input.message_seq }) : '') }
  else if (kind === 'image') { action = t("ui.lib.toolActivity.a9769c635970"); detail = name === 'read_images' ? '' : files[0] ?? firstText('url') }
  else if (kind === 'read') { action = t("ui.lib.toolActivity.407dd16f938f"); detail = files[0] ?? '' }
  else if (kind === 'edit') { action = t("ui.lib.toolActivity.533f9a4f1d10"); detail = files.map((path) => path.split(/[\\/]/).pop()).join(t('ui.common.list_separator')) }
  else if (kind === 'search') { action = t("ui.lib.toolActivity.44ce7ae909bb"); detail = firstText('pattern', 'query', 'path') }
  else if (kind === 'command') { action = t("ui.lib.toolActivity.e53f35dc006b"); detail = firstText('command', 'cmd', 'script', 'code', 'text') }
  else if (kind === 'web') { action = name === 'web_search' ? t("ui.lib.toolActivity.01aeaf3f0fde") : t("ui.lib.toolActivity.1846b9b97b84"); detail = firstText('query', 'url', 'action') }
  else if (kind === 'agent') {
    action = ({ spawn_agent: t("ui.lib.toolActivity.8db869874648"), followup_task: t("ui.toolActivity.followupAgent"), list_agents: t("ui.lib.toolActivity.07fe1db44a8e"), wait_agent: t("ui.lib.toolActivity.ff1841e287b5"), wait_agents: t("ui.lib.toolActivity.ff1841e287b5"), send_message: t("ui.lib.toolActivity.f979ee1f47a3"), interrupt_agent: t("ui.lib.toolActivity.d4b2fe048c2a") } as Record<string, string>)[name]!
    detail = firstText('description', 'target')
  } else if (kind === 'plan') { action = /^todo_(write|update|bind)$/.test(name) ? t("ui.lib.toolActivity.6253f06c73ac") : t("ui.lib.toolActivity.1c9c198d6fd3"); detail = firstText('objective', 'status') }
  else if (kind === 'skill') { action = t("ui.lib.toolActivity.11ff0491f783"); detail = firstText('name') }
  else if (name === 'show_widget') { action = t('ui.widget.activity'); detail = firstText('title') }
  else if (name === 'widget_guidelines') { action = t('ui.widget.activityGuidelines'); detail = '' }
  let label = running ? t("ui.lib.toolActivity.86b926e48b70", {p0: (action)}) : block.status === 'error' ? t("ui.lib.toolActivity.942be111a05e", {p0: (action)}) : t("ui.lib.toolActivity.276427644fe0", {p0: (action)})
  const display = block.display && typeof block.display === 'object' ? block.display as Record<string, unknown> : {}
  if (kind === 'edit' && block.status === 'ok' && display.changed === false) label = t("ui.lib.toolActivity.013fb568c311")
  if (!running && display.steering_deferred) label = t("ui.lib.toolActivity.ed7fc5fff884")
  if (!running && display.interrupted) label = kind === 'command' ? t("ui.lib.toolActivity.2671ac849b3e") : t("ui.lib.toolActivity.50c329bed561")
  if (!running && display.not_executed) label = t("ui.lib.toolActivity.9bd0c0e7f8db")
  if (kind === 'command' && !running && block.status === 'ok' && display.background) label = display.promoted ? t("ui.lib.toolActivity.e9dde79b523d") : t("ui.lib.toolActivity.5b351ec38480")
  if (kind === 'command') label = commandResult(block).label ?? label
  if (toolArgsFailure(block)) label = t('ui.toolArgs.rejected')
  if (fileWriteRejected(block)) label = t('ui.fileWrite.exists')
  return { kind, label, detail: detail.replace(/\s+/g, ' ').trim(), files: files.slice(0, 6) }
}
