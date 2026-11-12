import type {InspectorTab} from '@/types'

export const INSPECTOR_GROUPS = [
  {id: 'progress', labelKey: 'ui.workspace.launcher.progress', panels: ['todos', 'queue']},
  {id: 'diagnostics', labelKey: 'ui.workspace.launcher.diagnostics', panels: ['runtime', 'context', 'prompts']},
  {id: 'review', labelKey: 'ui.workspace.launcher.review', panels: ['changes', 'git']},
  {id: 'resources', labelKey: 'ui.workspace.launcher.resources', panels: ['files', 'browser', 'terminal']},
  {id: 'help', labelKey: 'ui.workspace.launcher.help', panels: ['academy']},
] as const

/** 检查器固定面板的入口、顺序和资源身份；成员页签仍按对话动态注册。 */
export const INSPECTOR_PANELS = [
  {value: 'home', labelKey: 'ui.trace.hub', keywords: 'launcher new tab workspace', diagnostic: false},
  {value: 'academy', labelKey: 'ui.academy.title', keywords: 'academy learn course harness tutorial', diagnostic: false},
  {value: 'todos', labelKey: 'ui.inspector.plansAndTodos', keywords: 'todos tasks plan', diagnostic: false},
  {value: 'queue', labelKey: 'ui.projectBoard.tasks', keywords: 'tasks queue runs', diagnostic: false},
  {value: 'changes', labelKey: 'ui.components.inspector.Inspector.582a8e42e44e', keywords: 'changes diff edits', diagnostic: false},
  {value: 'git', labelKey: 'ui.workspace.git.title', keywords: 'git worktrees diff commit branch stage', diagnostic: false},
  {value: 'files', labelKey: 'ui.components.inspector.Inspector.39932f24fe11', keywords: 'files explorer folder directory', diagnostic: false},
  {value: 'browser', labelKey: 'ui.components.inspector.Inspector.e19c3b9e266c', keywords: 'browser preview chrome chromium web', diagnostic: false},
  {value: 'terminal', labelKey: 'ui.components.inspector.Inspector.e2a76ef1f12e', keywords: 'terminal shell console bash zsh powershell', diagnostic: false},
  {value: 'runtime', labelKey: 'ui.components.inspector.Inspector.75b269496f69', keywords: 'runtime jobs background tasks', diagnostic: true},
  {value: 'context', labelKey: 'ui.components.inspector.Inspector.6fc38ef3602f', keywords: 'context tokens usage', diagnostic: true},
  {value: 'prompts', labelKey: 'ui.trace.title', keywords: 'trace requests tools execution prompts', diagnostic: true},
] as const satisfies ReadonlyArray<{value: InspectorTab; labelKey: string; keywords: string; diagnostic: boolean}>
