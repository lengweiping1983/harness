import { noticeText } from '@/lib/noticeText'
import {handlePageNavigationKeyDown} from '@/lib/tabNavigation'
import {SETTINGS_GROUPS, SETTINGS_PAGES, type SettingsPage, type SettingsGroup} from '@/lib/settingsCatalog'
import { t, useLocale } from '@/i18n'
import { Component, lazy, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  Blocks,
  BrainCircuit,
  Layers3,
  Server,
  ShieldCheck,
  Plug,
  Package,
  Clock3,
  GitBranch,
  Keyboard,
  Monitor,
  Moon,
  Puzzle,
  RotateCw,
  Search,
  SlidersHorizontal,
  Sun,
  Users,
  Wrench,
  X,
} from 'lucide-react'
import { cn, formatNumber } from '@/lib/utils'
import { searchCommands } from '@/lib/commandSearch'
import { groupModelsByProvider, modelDisplayName } from '@/lib/modelMenu'
import { modelSaveKey, useAppStore, DEFAULT_FONT_SIZE, MAX_FONT_SIZE, MIN_FONT_SIZE, type Theme } from '@/store/app'
import { Button, Kbd, Overlay } from '@/components/ui/primitives'
import { HOTKEY_ROWS, formatHotkey } from '@/lib/hotkeyCatalog'
import { useConnectionRoutes } from '@/lib/connectionRoutes'

const pageModules = {
  CapabilitiesPanel: () => import('./CapabilitiesPanel').then(module => ({default: module.CapabilitiesPanel})),
  BuiltinToolsPage: () => import('./BuiltinToolsPage').then(module => ({default: module.BuiltinToolsPage})),
  PluginsPage: () => import('./PluginsPage').then(module => ({default: module.PluginsPage})),
  ModelConnections: () => import('./ModelConnections').then(module => ({default: module.ModelConnections})),
  AutomationsPage: () => import('./AutomationsPage').then(module => ({default: module.AutomationsPage})),
  ProjectHooksPage: () => import('./ProjectHooksPage').then(module => ({default: module.ProjectHooksPage})),
  MemoryPanel: () => import('./MemoryPanel').then(module => ({default: module.MemoryPanel})),
  PersonalityPanel: () => import('./PersonalityPanel').then(module => ({default: module.PersonalityPanel})),
  AgentsPanel: () => import('./AgentsPanel').then(module => ({default: module.AgentsPanel})),
  McpServicePanel: () => import('./McpServicePanel').then(module => ({default: module.McpServicePanel})),
  EscPanel: () => import('./EscPanel').then(module => ({default: module.EscPanel})),
  SkillsPage: () => import('./EscPanel').then(module => ({default: module.SkillsPage})),
  ConnectorsPage: () => import('./EscPanel').then(module => ({default: module.ConnectorsPage})),
}
const CapabilitiesPanel = lazy(pageModules.CapabilitiesPanel)
const BuiltinToolsPage = lazy(pageModules.BuiltinToolsPage)
const PluginsPage = lazy(pageModules.PluginsPage)
const ModelConnections = lazy(pageModules.ModelConnections)
const AutomationsPage = lazy(pageModules.AutomationsPage)
const ProjectHooksPage = lazy(pageModules.ProjectHooksPage)
const MemoryPanel = lazy(pageModules.MemoryPanel)
const PersonalityPanel = lazy(pageModules.PersonalityPanel)
const AgentsPanel = lazy(pageModules.AgentsPanel)
const McpServicePanel = lazy(pageModules.McpServicePanel)
const EscPanel = lazy(pageModules.EscPanel)
const SkillsPage = lazy(pageModules.SkillsPage)
const ConnectorsPage = lazy(pageModules.ConnectorsPage)

/**
 * 设置面板。
 *
 * 设置按功能分栏：
 * 主题字号、模型、快捷键是三种完全不同的心智模型，混在一页里用户每次都要
 * 重新扫一遍才知道东西在哪。
 *
 * "模型"页是重点：默认模型 + 每个模型的可见性都**立即生效并落盘**。
 * 可见性勾掉之后该模型就不再出现在 model seat 里，但正在用的那个永远保留
 * （后端 ``/api/v1/models`` 保证），避免"关不掉也用不了"的死角。
 */

type Page = SettingsPage

const PAGE_ICONS = {SlidersHorizontal, BrainCircuit, ShieldCheck, Keyboard, Puzzle, Users, Blocks, Wrench, Layers3, Plug, Package, Server, GitBranch, Clock3}

/** 只预取导航意图指向的模块，不挂载页面或启动页面的数据观察。 */
const pagePreloads: Partial<Record<Page, () => Promise<unknown>>> = {
  models: pageModules.ModelConnections, permissions: pageModules.CapabilitiesPanel,
  tools: pageModules.BuiltinToolsPage, plugins: pageModules.PluginsPage,
  automations: pageModules.AutomationsPage, hooks: pageModules.ProjectHooksPage,
  memory: pageModules.MemoryPanel, personality: pageModules.PersonalityPanel,
  agents: () => Promise.all([pageModules.EscPanel(), pageModules.AgentsPanel()]),
  mcpServer: pageModules.McpServicePanel, skills: pageModules.SkillsPage, connectors: pageModules.ConnectorsPage,
}
function preloadPage(page: Page) {void pagePreloads[page]?.().catch(() => { /* 所属加载边界呈现错误。 */ })}

const THEMES: Array<{ id: Theme; label: string; icon: typeof Sun }> = [
  { id: 'light', get label() { return t("ui.components.overlays.SettingsDialog.aa0819dfc4d8") }, icon: Sun },
  { id: 'dark', get label() { return t("ui.components.overlays.SettingsDialog.a6b75d068032") }, icon: Moon },
  { id: 'system', get label() { return t("ui.components.overlays.SettingsDialog.217cfe7db1e3") }, icon: Monitor },
]

export function SettingsDialog() {
  useLocale()

  const open = useAppStore((s) => s.settingsOpen)
  const target = useAppStore((s) => s.settingsTarget)
  const setOpen = useAppStore((s) => s.setSettingsOpen)
  const [page, setPage] = useState<Page>('general')
  const [agentKind, setAgentKind] = useState<'all' | 'agent' | 'team'>('all')
  const [shortcutQuery, setShortcutQuery] = useState('')
  const [settingsQuery, setSettingsQuery] = useState('')
  const [expandedGroups, setExpandedGroups] = useState<SettingsGroup[]>(() => SETTINGS_GROUPS.map(group => group.id))
  const contentRef = useRef<HTMLElement | null>(null)
  const matchingPages = searchCommands(SETTINGS_PAGES.map(item => ({
    ...item,
    title: t(item.labelKey),
    keywords: t(item.keywordsKey),
  })), settingsQuery)
  useEffect(() => {
    const group = SETTINGS_PAGES.find(item => item.id === page)?.group
    if (group) setExpandedGroups(current => current.includes(group) ? current : [...current, group])
  }, [page, open])
  useEffect(() => { if (open) setSettingsQuery('') }, [open])
  const navRef = useRef<HTMLElement | null>(null)
  useEffect(() => {
    if (!open || !target) return
    if (target === 'roles' || target === 'teams') {
      setAgentKind(target === 'roles' ? 'agent' : 'team'); setPage('agents')
    } else { setAgentKind('all'); setPage(target) }
  }, [open, target])
  useEffect(() => {
    if (!open) return
    const reveal = () => navRef.current?.querySelector<HTMLElement>('[aria-current="page"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    reveal()
    window.addEventListener('resize', reveal)
    return () => window.removeEventListener('resize', reveal)
  }, [open, page])
  useEffect(() => {
    if (open) contentRef.current?.scrollTo({top: 0, left: 0})
  }, [open, page])

  if (!open) return null

  return (
    <Overlay onClose={() => setOpen(false)} onEscape={event => {
      const target = event.target
      if (target instanceof HTMLInputElement && target.type === 'search' && settingsQuery) {
        setSettingsQuery('')
        return true
      }
      return false
    }} className="flex h-settings max-w-6xl flex-col p-0 sm:h-settings-wide" labelledBy="settings-title">
      <header className="flex shrink-0 items-center justify-between border-b border-line px-4 py-2">
        <h2 id="settings-title" className="ui-section-title">{t("ui.components.overlays.SettingsDialog.df3d58c7d84b")}</h2>
        <div className="ui-input-group mx-3 flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-raised/60 px-2.5 sm:ml-auto sm:max-w-xs">
          <Search aria-hidden className="h-3.5 w-3.5 shrink-0 text-faint" />
          <input type="search" value={settingsQuery} onChange={event => setSettingsQuery(event.target.value)}
            aria-label={t('ui.settings.search')} placeholder={t('ui.settings.search')}
            className="h-8 min-w-0 w-full bg-transparent text-xs outline-none"
            onKeyDown={event => {
              if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return
              if (event.key === 'Escape') {
                event.preventDefault()
                event.stopPropagation()
                setSettingsQuery('')
                return
              }
              if (event.key === 'Enter' && matchingPages[0]) {
                event.preventDefault(); setPage(matchingPages[0].id); setAgentKind('all')
                navRef.current?.querySelectorAll<HTMLButtonElement>('button').forEach(button => {
                  if (button.dataset.page === matchingPages[0].id) button.focus()
                })
              }
            }} />
        </div>
        <button type="button" onClick={() => setOpen(false)} aria-label={t("ui.components.overlays.SettingsDialog.77b17fc4a52a")}
          className="flex h-10 w-10 items-center justify-center rounded-lg text-faint transition-colors hover:bg-raised hover:text-ink">
          <X className="h-4 w-4" />
        </button>
      </header>
      <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-stacked sm:grid-cols-settings sm:grid-rows-fill">
        <nav ref={navRef} onKeyDown={handlePageNavigationKeyDown} aria-label={t("ui.components.overlays.SettingsDialog.dfb699c54378")} className="min-h-0 max-h-48 overflow-y-auto border-b border-line bg-raised/60 p-2 sm:max-h-none sm:border-b-0 sm:border-r">
          {!matchingPages.length ? <p role="status" className="px-2 py-3 text-xs text-muted">{t('ui.settings.searchEmpty')}</p> : null}
          {SETTINGS_GROUPS.map(group => {
            const entries = matchingPages.filter(item => item.group === group.id)
            if (!entries.length) return null
            const expanded = Boolean(settingsQuery.trim()) || expandedGroups.includes(group.id)
            return <section key={group.id} className="mb-1" aria-label={t(group.labelKey)}>
              <button type="button" aria-expanded={expanded} aria-controls={'settings-group-' + group.id}
                className="ui-group-title flex min-h-9 w-full items-center justify-between rounded-lg px-2 text-left text-muted hover:bg-raised"
                onClick={() => setExpandedGroups(current => current.includes(group.id) ? current.filter(id => id !== group.id) : [...current, group.id])}>
                {t(group.labelKey)}<span aria-hidden>{expanded ? '−' : '+'}</span>
              </button>
              <div id={'settings-group-' + group.id} hidden={!expanded} className={cn('grid-cols-2 gap-0.5 sm:grid-cols-1', expanded ? 'grid' : 'hidden')}>
          {entries.map((item) => {
            const Icon = PAGE_ICONS[item.icon]
            return (
              <button
                key={item.id}
                data-page={item.id}
                onPointerEnter={() => preloadPage(item.id)}
                onFocus={() => preloadPage(item.id)}
                onPointerDown={() => preloadPage(item.id)}
                type="button"
                onClick={() => { setPage(item.id); setAgentKind('all') }}
                aria-current={page === item.id ? 'page' : undefined}
                aria-controls="settings-content"
                className={cn(
                  'flex min-h-10 shrink-0 items-center gap-2 rounded-lg px-3 py-2 text-left text-xs transition-colors sm:min-h-0 sm:px-2 sm:py-1.5',
                  page === item.id ? 'bg-brand/[0.12] font-medium text-brand' : 'text-muted hover:bg-raised hover:text-ink',
                )}
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                {item.title}
              </button>
            )
          })}</div></section>})}
        </nav>

        <section ref={contentRef} id="settings-content" data-page={page} role="region" aria-label={t("ui.components.overlays.SettingsDialog.ed39c8a121d7")} className="min-h-0 min-w-0 overflow-y-auto overscroll-contain p-4">
          <SettingsPageBoundary key={page}><Suspense fallback={<SettingsPageLoading />} >
          {page === 'general' ? <GeneralPage /> : null}
          {page === 'models' ? <><ModelConnections /><ModelsPage /></> : null}
          {page === 'shortcuts' ? <ShortcutsPage query={shortcutQuery} setQuery={setShortcutQuery} /> : null}
          {page === 'automations' ? <AutomationsPage /> : null}
          {page === 'hooks' ? <ProjectHooksPage /> : null}
          {page === 'memory' ? <MemoryPanel /> : null}
          {page === 'personality' ? <PersonalityPanel /> : null}
          {page === 'agents' ? <div className="space-y-5">
            <EscPanel initialAgentKind={agentKind} catalog="agents" />
            <details className="rounded-xl border border-line p-3 text-xs text-muted">
              <summary className="cursor-pointer font-medium">{t('ui.agents.externalTitle')}</summary>
              <div className="mt-4"><AgentsPanel externalOnly onNavigate={target => {
                if (target === 'worktrees') {
                  setOpen(false)
                  useAppStore.getState().setInspectorTab('git')
                } else {
                  setAgentKind(target === 'roles' ? 'agent' : 'team')
                  setPage('agents')
                }
              }} /></div>
            </details>
          </div> : null}
          {page === 'skills' ? <SkillsPage /> : null}
          {page === 'tools' ? <BuiltinToolsPage /> : null}
          {page === 'connectors' ? <ConnectorsPage /> : null}
          {page === 'plugins' ? <PluginsPage /> : null}
          {page === 'mcpServer' ? <McpServicePanel /> : null}
          {page === 'permissions' ? <CapabilitiesPanel /> : null}
          </Suspense></SettingsPageBoundary>
        </section>
      </div>
    </Overlay>
  )
}

/* ============================ 通用 ============================ */

function GeneralPage() {
  useLocale()
  const language = useAppStore((s) => s.language)
  const languageSaving = useAppStore((s) => s.languageSaving)
  const languageError = useAppStore((s) => s.languageError)
  const setLanguage = useAppStore((s) => s.setLanguage)


  const theme = useAppStore((s) => s.theme)
  const setTheme = useAppStore((s) => s.setTheme)
  const fontSize = useAppStore((s) => s.uiFontSize)
  const setFontSize = useAppStore((s) => s.setUiFontSize)

  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <Row label={t('settings.language.label')} hint={t('settings.language.hint')}>
          <select id="language-preference" aria-label={t('settings.language.label')} disabled={languageSaving}
            value={language} onChange={(event) => void setLanguage(event.target.value as 'auto' | 'zh-CN' | 'en')}
            className="max-w-full rounded-lg border border-line bg-raised px-3 py-2 text-xs text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">
            <option value="auto">{t('settings.language.auto')}</option>
            <option value="zh-CN">中文</option>
            <option value="en">English</option>
          </select>
        </Row>
        {languageSaving ? <p role="status" className="px-0.5 text-2xs leading-relaxed text-faint">{t('settings.language.saving')}</p> : null}
        {languageError ? <div role="alert" className="space-y-1 text-xs leading-relaxed text-danger"><p>{noticeText(languageError)}</p>
          <button type="button" className="inline-flex items-center gap-1 rounded underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" onClick={() => void useAppStore.getState().refreshLanguage()}><RotateCw aria-hidden className="h-3 w-3" />{t('settings.language.reload')}</button>
        </div> : null}
      </section>

      <section className="space-y-2">
        <h3 className="ui-group-title">{t("ui.components.overlays.SettingsDialog.86a63f23a076")}</h3>

        <Row label={t("ui.components.overlays.SettingsDialog.788db1cfec2a")} hint={t("ui.components.overlays.SettingsDialog.1a36cfb6ad69")}>
          <div className="flex max-w-full flex-wrap items-center gap-1.5">
            {THEMES.map((item) => {
              const Icon = item.icon
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setTheme(item.id)}
                  aria-pressed={theme === item.id}
                  className={cn(
                    'inline-flex min-h-8 items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-compact transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand',
                    theme === item.id ? 'border-brand bg-brand/15 font-medium text-brand' : 'border-line-strong bg-surface text-muted hover:border-brand hover:bg-raised hover:text-ink',
                  )}
                >
                  <Icon className="h-3 w-3" />
                  {item.label}
                </button>
              )
            })}
          </div>
        </Row>

        <Row label={t("ui.components.overlays.SettingsDialog.6089721beb10")} hint={t("ui.components.overlays.SettingsDialog.c8f45d1c45d9", {p0: (fontSize)})}>
          <div className="flex max-w-full flex-wrap items-center gap-2">
            <input
              type="range"
              min={MIN_FONT_SIZE}
              max={MAX_FONT_SIZE}
              step={1}
              value={fontSize}
              onChange={(event) => setFontSize(Number(event.target.value))}
              aria-label={t("ui.components.overlays.SettingsDialog.6089721beb10")}
              className="max-w-full w-40 accent-brand"
            />
            <span className="w-8 text-right text-2xs tabular-nums text-muted">{fontSize}px</span>
            <Button size="xs" variant="ghost" onClick={() => setFontSize(DEFAULT_FONT_SIZE)} disabled={fontSize === DEFAULT_FONT_SIZE}>{t("ui.components.overlays.SettingsDialog.cb5d682bac3d")}</Button>
          </div>
        </Row>
      </section>

      <section className="space-y-2">
        <h3 className="ui-group-title">{t("ui.components.overlays.SettingsDialog.a5574109f020")}</h3>
        <Row label={t("ui.components.overlays.SettingsDialog.81855c069e22")} hint={t("ui.components.overlays.SettingsDialog.508fc03e3224")} description={t("ui.components.overlays.SettingsDialog.c92eca635bc8")}>
          <span className="text-2xs text-muted">
            {t("ui.components.overlays.SettingsDialog.6dceff73f657")}</span>
        </Row>
      </section>
    </div>
  )
}

function Row({ label, hint, description, children }: { label: string; hint?: string; description?: string; children: React.ReactNode }) {
  useLocale()

  return (
    <div className="flex flex-col items-start gap-3 rounded-xl border border-line px-3 py-2.5 sm:flex-row sm:gap-4">
      <div className="min-w-0 w-full sm:flex-1">
        <p className="text-xs font-medium text-ink">{label}</p>
        {hint ? <p className="mt-0.5 text-2xs leading-relaxed text-faint">{hint}</p> : null}
        {description ? <p className="mt-2 text-compact leading-relaxed text-muted">{description}</p> : null}
      </div>
      <div className="max-w-full shrink-0">{children}</div>
    </div>
  )
}

/* ============================ 模型 ============================ */

function ModelsPage() {
  const locale = useLocale()

  const routing = useConnectionRoutes()
  const models = useAppStore((s) => s.models)
  const defaultModel = useAppStore((s) => s.defaultModel)
  const visibility = useAppStore((s) => s.modelVisibility)
  const visibilitySaves = useAppStore((s) => s.modelVisibilitySaves)
  const setDefaultModel = useAppStore((s) => s.setDefaultModel)
  const modelSaving = useAppStore((s) => Boolean(s.modelSaves[modelSaveKey(null)]))
  const modelSave = useAppStore((s) => s.defaultModelSave)
  const dismissFeedback = useAppStore((s) => s.dismissDefaultModelFeedback)
  const setModelVisibility = useAppStore((s) => s.setModelVisibility)
  const groups = useMemo(() => groupModelsByProvider(models), [models, locale])

  if (routing?.models.length) return null

  return (
    <div className="space-y-4">
      <header>
        <h3 className="ui-section-title">{t("ui.components.overlays.SettingsDialog.c98e118e0a43")}</h3>
        <p className="mt-0.5 text-2xs leading-relaxed text-faint">{t("ui.components.overlays.SettingsDialog.60044c2733c0")}</p>
      </header>

      {modelSaving && modelSave ? <p role="status" aria-label={t("ui.components.overlays.SettingsDialog.88de44b26b76")} className="rounded-lg border border-line bg-raised p-3 text-xs text-muted [overflow-wrap:anywhere]">{t('ui.settings.models.savingDefault', {model: modelDisplayName(modelSave.target, models, t('ui.models.unavailableName'))})}</p> : null}
      {!modelSaving && modelSave?.error ? <div role="alert" aria-label={t("ui.components.overlays.SettingsDialog.fa8a07865d96")} className="space-y-2 rounded-lg border border-danger/30 bg-danger/5 p-3 text-xs text-danger [overflow-wrap:anywhere]">
        <p>{modelSave.unconfirmed ? t("ui.components.overlays.SettingsDialog.56ff57932ddb") : t("ui.components.overlays.SettingsDialog.9ce847de7d72")}</p>
        <p tabIndex={0} className="max-h-24 overflow-y-auto">{noticeText(modelSave.error)}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="xs" className="h-auto min-h-6 max-w-full whitespace-normal py-1 text-left [overflow-wrap:anywhere]" onClick={() => void setDefaultModel(modelSave.target)}>{t('ui.settings.models.retryDefault', {model: modelDisplayName(modelSave.target, models, t('ui.models.unavailableName'))})}</Button>
          <Button size="xs" variant="ghost" onClick={dismissFeedback}>{t("ui.components.overlays.SettingsDialog.a7aa73da9350")}</Button>
        </div>
      </div> : null}

      {groups.map((group) => (
        <section key={JSON.stringify(group.provider)} className="space-y-1.5">
          <h4 className="ui-group-title">{group.provider ?? t('ui.models.unspecifiedProvider')}</h4>
          {group.models.map((model) => {
            const visible = visibility[model.id] !== false
            const isDefault = model.id === defaultModel
            const visibilitySave = visibilitySaves[model.id]
            return (
              <div
                key={model.id}
                className="grid grid-cols-form-row items-start gap-x-3 gap-y-2 rounded-xl border border-line px-3 py-2 sm:flex sm:items-center sm:gap-3"
              >
                <button
                  type="button"
                  role="switch"
                  aria-checked={visible}
                  disabled={visibilitySave?.pending}
                  aria-busy={visibilitySave?.pending || undefined}
                  onClick={() => void setModelVisibility(model.id, !visible)}
                  className={cn(
                    'relative flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus-visible:ring-2 focus-visible:ring-brand',
                    visible ? 'border-brand bg-brand' : 'border-line-strong bg-raised',
                    'disabled:cursor-wait disabled:opacity-50',
                  )}
                  aria-label={t(visible ? 'ui.settings.models.hideNamed' : 'ui.settings.models.showNamed', {model: model.label})}
                >
                  <span aria-hidden="true" className={cn('absolute left-0.5 h-3.5 w-3.5 rounded-full transition-transform', visible ? 'translate-x-4 bg-brand-ink' : 'bg-ink')} />
                </button>

                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-ink [overflow-wrap:anywhere] sm:truncate">{model.label}</p>
                  {!model.id.startsWith('harness/') ? <p className="text-2xs text-faint [overflow-wrap:anywhere] sm:truncate">
                    {model.id}
                    {model.context_window ? t("ui.components.overlays.SettingsDialog.dbbab1bafe6b", {p0: (formatNumber(model.context_window))}) : ''}
                  </p> : model.context_window ? <p className="text-2xs text-faint">{t('ui.models.contextSize', {count: formatNumber(model.context_window)})}</p> : null}
                  {visibilitySave?.pending ? <p role="status" aria-label={t("ui.components.overlays.SettingsDialog.dd9d81b48619")} className="mt-1 text-2xs text-muted">{t(visibilitySave.target ? 'ui.settings.models.showing' : 'ui.settings.models.hiding', {model: model.label})}</p> : null}
                  {!visibilitySave?.pending && visibilitySave?.error ? <div role="alert" aria-label={t("ui.components.overlays.SettingsDialog.cc6f6f1dda23")} className="mt-1 space-y-1 text-2xs text-danger [overflow-wrap:anywhere]">
                    <p>{visibilitySave.unconfirmed ? t("ui.components.overlays.SettingsDialog.3b189be96200") : t("ui.components.overlays.SettingsDialog.cbbbe96b2b34")}</p>
                    <p tabIndex={0} className="max-h-20 overflow-y-auto">{noticeText(visibilitySave.error)}</p>
                    <button type="button" className="text-brand underline" onClick={() => void setModelVisibility(model.id, visibilitySave.target)}>{t(visibilitySave.target ? 'ui.settings.models.retryShow' : 'ui.settings.models.retryHide', {model: model.label})}</button>
                  </div> : null}
                </div>

                <Button
                  size="xs"
                  className="col-start-2 justify-self-start sm:shrink-0"
                  variant={isDefault ? 'primary' : 'secondary'}
                  disabled={isDefault || modelSaving}
                  onClick={() => void setDefaultModel(model.id)}
                >
                  {modelSaving && modelSave?.target === model.id ? t("ui.components.overlays.SettingsDialog.ff509c9ba052") : isDefault ? t("ui.components.overlays.SettingsDialog.ba336585f0a2") : t("ui.components.overlays.SettingsDialog.357211096f78")}
                </Button>
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}

/* ============================ 快捷键 ============================ */

function ShortcutsPage({ query, setQuery }: { query: string; setQuery: (v: string) => void }) {
  const locale = useLocale()

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return HOTKEY_ROWS
    return HOTKEY_ROWS.filter((row) =>
      [row.group, row.label, row.keys, formatHotkey(row.keys)].some((v) => v.toLowerCase().includes(q)),
    )
  }, [query, locale])

  return (
    <div className="space-y-3">
      <header>
        <h3 className="ui-section-title">{t("ui.components.overlays.SettingsDialog.ee2638183d3e")}</h3>
        <p className="mt-0.5 text-2xs text-faint">{t("ui.components.overlays.SettingsDialog.dd145041442a")}</p>
      </header>

      <div className="ui-input-group flex items-center gap-2 rounded-lg border border-line px-3 py-2">
        <Search className="h-3.5 w-3.5 shrink-0 text-faint" />
        <input
          autoFocus
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={t("ui.components.overlays.SettingsDialog.6769c268b3b7")}
          aria-label={t("ui.components.overlays.SettingsDialog.49504fdfc1b5")}
          className="min-w-0 flex-1 bg-transparent text-xs text-ink placeholder:text-faint focus:outline-none"
        />
        {query ? (
          <button type="button" onClick={() => setQuery('')} className="text-2xs text-faint hover:text-ink">{t("ui.components.overlays.SettingsDialog.bce2377283c2")}</button>
        ) : null}
      </div>

      {rows.length === 0 ? (
        <p className="py-6 text-center text-xs text-faint">{t("ui.components.overlays.SettingsDialog.078e8e583865")}</p>
      ) : (
        rows.map((row) => (
          <div key={row.label} className="flex items-center gap-3 px-1 py-1.5">
            <span className="w-16 shrink-0 text-2xs text-faint">{row.group}</span>
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{row.label}</span>
            <span className="flex shrink-0 items-center gap-1">
              {formatHotkey(row.keys).split('+').map((key) => (
                <Kbd key={key}>{key}</Kbd>
              ))}
            </span>
          </div>
        ))
      )}
    </div>
  )
}


function SettingsPageFailure() {
  useLocale()
  return <div className="space-y-3"><p role="alert" className="text-xs leading-relaxed text-danger">{t('ui.deferred.settings.failed')}</p><Button size="sm" icon={<RotateCw aria-hidden className="h-3.5 w-3.5" />} onClick={() => window.location.reload()}>{t('ui.deferred.reload')}</Button></div>
}

function SettingsPageLoading() {
  useLocale()
  const [expired, setExpired] = useState(false)
  useEffect(() => {
    const timer = setTimeout(() => setExpired(true), 10000)
    return () => clearTimeout(timer)
  }, [])
  return expired ? <SettingsPageFailure /> : <div role="status" aria-label={t('ui.deferred.settings.loading')} className="space-y-4">
    <p className="text-xs text-muted">{t('ui.deferred.settings.loading')}</p>
    <div aria-hidden="true" className="space-y-3">
      <div className="shimmer-bar h-5 w-36 rounded" />
      <div className="shimmer-bar h-16 w-full rounded-lg" />
      <div className="shimmer-bar h-16 w-full rounded-lg" />
    </div>
  </div>
}

class SettingsPageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <SettingsPageFailure /> : this.props.children }
}
