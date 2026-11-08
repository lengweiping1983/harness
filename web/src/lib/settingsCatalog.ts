/** 设置目录是导航、搜索与命令面板的共同事实源。 */
export const SETTINGS_GROUPS = [
  {id: 'basic', labelKey: 'ui.settings.groups.basic'},
  {id: 'agents', labelKey: 'ui.settings.groups.agents'},
  {id: 'connections', labelKey: 'ui.settings.groups.connections'},
  {id: 'advanced', labelKey: 'ui.settings.groups.advanced'},
] as const

export type SettingsGroup = typeof SETTINGS_GROUPS[number]['id']
export const SETTINGS_PAGES = [
  {id: 'general', group: 'basic', labelKey: 'ui.components.overlays.SettingsDialog.835b700e028c', keywordsKey: 'ui.settings.keywords.general', icon: 'SlidersHorizontal'},
  {id: 'models', group: 'basic', labelKey: 'ui.components.overlays.SettingsDialog.c98e118e0a43', keywordsKey: 'ui.settings.keywords.models', icon: 'BrainCircuit'},
  {id: 'permissions', group: 'basic', labelKey: 'ui.components.overlays.SettingsDialog.cec99e7fda96', keywordsKey: 'ui.settings.keywords.permissions', icon: 'ShieldCheck'},
  {id: 'shortcuts', group: 'basic', labelKey: 'ui.components.overlays.SettingsDialog.ee2638183d3e', keywordsKey: 'ui.settings.keywords.shortcuts', icon: 'Keyboard'},
  {id: 'voice', group: 'basic', labelKey: 'ui.voice.dictation.title', keywordsKey: 'ui.voice.dictation.keywords', icon: 'Mic'},
  {id: 'personality', group: 'agents', labelKey: 'ui.personality.heading', keywordsKey: 'ui.settings.keywords.personality', icon: 'Puzzle'},
  {id: 'agents', group: 'agents', labelKey: 'ui.agents.directory', keywordsKey: 'ui.settings.keywords.agents', icon: 'Users'},
  {id: 'skills', group: 'agents', labelKey: 'ui.extensions.skills', keywordsKey: 'ui.extensions.skillsKeywords', icon: 'Blocks'},
  {id: 'tools', group: 'agents', labelKey: 'ui.settings.tools.title', keywordsKey: 'ui.settings.tools.keywords', icon: 'Wrench'},
  {id: 'superagent', group: 'agents', labelKey: 'ui.sg.title', keywordsKey: 'ui.sg.keywords', icon: 'GitBranch'},
  {id: 'memory', group: 'agents', labelKey: 'ui.memory.heading', keywordsKey: 'ui.settings.keywords.memory', icon: 'Layers3'},
  {id: 'connectors', group: 'connections', labelKey: 'ui.extensions.connectors', keywordsKey: 'ui.extensions.connectorsKeywords', icon: 'Plug'},
  {id: 'plugins', group: 'connections', labelKey: 'ui.extensions.plugins', keywordsKey: 'ui.extensions.pluginsKeywords', icon: 'Package'},
  {id: 'mcpServer', group: 'connections', labelKey: 'ui.mcpService.title', keywordsKey: 'ui.mcpService.keywords', icon: 'Server'},
  {id: 'hooks', group: 'advanced', labelKey: 'ui.components.overlays.SettingsDialog.926b04e8ced4', keywordsKey: 'ui.settings.keywords.hooks', icon: 'GitBranch'},
  {id: 'automations', group: 'advanced', labelKey: 'ui.components.overlays.SettingsDialog.3c39e9bde120', keywordsKey: 'ui.settings.keywords.automations', icon: 'Clock3'},
] as const satisfies ReadonlyArray<{id: string; group: SettingsGroup; labelKey: string; keywordsKey: string; icon: string}>
export type SettingsPage = typeof SETTINGS_PAGES[number]['id']

export const availableSettingsPages = (voiceSupported: boolean) => SETTINGS_PAGES.filter(page => page.id !== 'voice' || voiceSupported)
