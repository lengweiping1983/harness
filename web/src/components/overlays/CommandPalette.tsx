import { noticeText } from '@/lib/noticeText'
import {failureText} from '@/lib/resourceError'
import {INSPECTOR_PANELS} from '@/lib/inspectorPanels'
import {availableSettingsPages} from '@/lib/settingsCatalog'
import {useVoiceInputSupported} from '@/hooks/useVoiceInputSupported'
import { t, useLocale } from '@/i18n'
import { useEffect, useLayoutEffect, useId, useMemo, useRef, useState } from 'react'
import {
  Check,
  CornerDownLeft,
  FileCode,
  Folder,
  Gauge,
  Moon,
  Plus,
  Search,
  Sun,
  Wrench,
  X,
} from 'lucide-react'
import { cn, isMac } from '@/lib/utils'
import { HOTKEY_ROWS, formatHotkey } from '@/lib/hotkeyCatalog'
import { showLatestChanges } from '@/store/changeCards'
import { useAppStore } from '@/store/app'
import { searchCommands } from '@/lib/commandSearch'
import { api } from '@/lib/api'
import type { ResourceText, WorkspacePayload } from '@/types'
import { normalizePath } from '@/lib/workspaces'
import { Badge, IconButton, Kbd, Overlay } from '@/components/ui/primitives'

/**
 * 命令面板（Cmd/Ctrl+K）。
 *
 * 单一入口聚合所有"点两下才能完成"的操作：切会话 / 新建 / 切主题 /
 * 切检查器 tab / 打开工作区文件。支持模糊匹配（子序列打分），
 * 中文按字符匹配，不依赖分词。
 */

interface Command {
  id: string
  title: string
  hint?: string
  keywords?: string
  group: string
  icon: React.ReactNode
  run: () => void
}

export function CommandPalette() {
  const locale = useLocale()

  const open = useAppStore((s) => s.commandOpen)
  const voiceSupported = useVoiceInputSupported(open)
  const setOpen = useAppStore((s) => s.setCommandOpen)
  const sessions = useAppStore((s) => s.sessions)
  const activeSessionId = useAppStore((s) => s.activeSessionId)
  const select = useAppStore((s) => s.selectSession)
  const create = useAppStore((s) => s.startNewSession)
  const theme = useAppStore((s) => s.theme)
  const setTheme = useAppStore((s) => s.setTheme)
  const setSettingsOpen = useAppStore((s) => s.setSettingsOpen)
  const setInspectorTab = useAppStore((s) => s.setInspectorTab)
  const workspace = useAppStore((s) => s.workspace)
  const workspaces = useAppStore((s) => s.workspaces)
  const activeWorkspaceId = useAppStore((s) => s.activeWorkspaceId)
  const revealFile = useAppStore((s) => s.revealInFiles)
  const root = workspaces.find((item) => item.id === activeWorkspaceId)?.path ?? workspace?.root ?? null
  const toggleSidebar = useAppStore((s) => s.toggleSidebar)
  const toggleInspector = useAppStore((s) => s.toggleInspector)

  const [query, setQuery] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const formId = useId()
  const listRef = useRef<HTMLDivElement | null>(null)
  const [files, setFiles] = useState<{ requestedRoot: string; payload: WorkspacePayload } | null>(null)
  const [fileError, setFileError] = useState<ResourceText>('')
  const [fileLoading, setFileLoading] = useState(false)
  const [fileRetry, setFileRetry] = useState(0)
  const [statusRoot, setStatusRoot] = useState<string | null>(null)
  useEffect(() => {
    if (!open || !root) return
    let alive = true
    const controller = new AbortController()
    setFiles(null); setFileError(''); setFileLoading(true); setStatusRoot(root)
    void api.workspace('', root, {signal: controller.signal}).then((result) => {
      if (!alive) return
      if (result.ok && normalizePath(result.data.root) !== normalizePath(root)) setFileError({key: "ui.components.overlays.CommandPalette.cbf19838304f"})
      else if (result.ok && result.data.exists && result.data.is_dir) setFiles({ requestedRoot: root, payload: result.data })
      else setFileError(result.ok ? {key: "ui.components.overlays.CommandPalette.3177e3d2fabb"} : result.copy ?? result.message)
    }).catch((error) => { if (alive) setFileError(failureText(error)) })
      .finally(() => { if (alive) setFileLoading(false) })
    return () => { alive = false; controller.abort() }
  }, [open, root, fileRetry])

  useLayoutEffect(() => {
    if (open) {
      setQuery('')
      setActiveId(null)
    }
  }, [open])

  const commands = useMemo<Command[]>(() => {
    const out: Command[] = [
      ...(activeSessionId ? [{
        id: 'quick-open-file',
        title: t('ui.quickOpen.title'),
        hint: t('ui.quickOpen.shortcut'),
        keywords: 'quick open file find path search',
        group: t('ui.commands.files'),
        icon: <FileCode className="h-3.5 w-3.5" />,
        run: () => useAppStore.getState().setQuickOpen(true),
      }] : []),
      ...availableSettingsPages(voiceSupported).map(({id: target, labelKey, keywordsKey}) => ({
        id: `settings-${target}`,
        title: t(labelKey),
        keywords: t(keywordsKey),
        group: t('ui.components.overlays.SettingsDialog.df3d58c7d84b'),
        icon: <Wrench className="h-3.5 w-3.5" />,
        run: () => setSettingsOpen(true, target),
      })),
      {
        id: 'new-session',
        title: t("ui.components.overlays.CommandPalette.58e21b8737cc"),
        group: t("ui.components.overlays.CommandPalette.a63280253f17"),
        icon: <Plus className="h-3.5 w-3.5" />,
        run: () => void create(),
      },
      {
        id: 'show-turn-changes',
        title: t("ui.components.overlays.CommandPalette.81073de22559"),
        hint: t("ui.components.overlays.CommandPalette.85fae60ebbd9"),
        group: t("ui.components.overlays.CommandPalette.3c8c5939d685"),
        icon: <FileCode className="h-3.5 w-3.5" />,
        run: showLatestChanges,
      },
      {
        id: 'toggle-theme',
        title: theme === 'dark' ? t("ui.components.overlays.CommandPalette.d2749fb9674c") : t("ui.components.overlays.CommandPalette.33b555e4dff9"),
        group: t("ui.components.overlays.CommandPalette.86a63f23a076"),
        icon: theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />,
        run: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
      },
      {
        id: 'toggle-sidebar',
        title: t("ui.components.overlays.CommandPalette.3126db42b37a"),
        group: t("ui.components.overlays.CommandPalette.86a63f23a076"),
        icon: <Folder className="h-3.5 w-3.5" />,
        run: () => toggleSidebar(),
      },
      {
        id: 'toggle-inspector',
        title: t("ui.components.overlays.CommandPalette.c666fc9798f1"),
        group: t("ui.components.overlays.CommandPalette.86a63f23a076"),
        icon: <Gauge className="h-3.5 w-3.5" />,
        run: () => toggleInspector(),
      },
      ...INSPECTOR_PANELS.map((panel) => ({
        id: `tab-${panel.value}`,
        keywords: panel.keywords,
        title: t("ui.components.overlays.CommandPalette.5dacde371a07", {p0: t(panel.labelKey)}),
        group: t("ui.components.overlays.CommandPalette.3c8c5939d685"),
        icon: panel.value === 'files' ? <Folder className="h-3.5 w-3.5" /> : <Wrench className="h-3.5 w-3.5" />,
        run: () => setInspectorTab(panel.value),
      })),
    ]

    for (const session of sessions) {
      out.push({
        id: `session-${session.id}`,
        title: session.title || t("ui.components.overlays.CommandPalette.a91ae6035092"),
        hint: session.cwd ?? undefined,
        group: t("ui.components.overlays.CommandPalette.a63280253f17"),
        icon: <Search className="h-3.5 w-3.5" />,
        run: () => select(session.id),
      })
    }

    for (const entry of files?.requestedRoot === root ? files.payload.entries : []) {
      out.push({
        id: `file-${entry.path}`,
        title: entry.name,
        hint: entry.path,
        group: entry.type === 'directory' ? t("ui.components.overlays.CommandPalette.52daa71ebc31") : t("ui.components.overlays.CommandPalette.39932f24fe11"),
        icon: entry.type === 'directory' ? <Folder className="h-3.5 w-3.5" /> : <FileCode className="h-3.5 w-3.5" />,
        run: () => revealFile(entry.path, undefined, { root: files!.payload.root, directory: entry.type === 'directory' }),
      })
    }

    return out
  }, [
    activeSessionId,
    voiceSupported,
    create,
    select,
    sessions,
    setInspectorTab,
    setSettingsOpen,
    setTheme,
    theme,
    toggleInspector,
    toggleSidebar,
    files,
    root,
    revealFile,
  , locale])

  const results = useMemo(() => searchCommands(commands, query), [commands, query, locale])
  const cursor = Math.max(0, results.findIndex((command) => command.id === activeId))
  const optionId = (id: string) => `${formId}-option-${encodeURIComponent(id)}`

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [cursor, results])

  if (!open) return null

  const runAt = (index: number) => {
    const command = results[index]
    if (!command) return
    setOpen(false)
    command.run()
  }

  return (
    <Overlay onClose={() => setOpen(false)} labelledBy={`${formId}-title`} className="max-w-xl">
      <h2 id={`${formId}-title`} className="sr-only">{t("ui.components.overlays.CommandPalette.a72832b006c1")}</h2>
      <div className="flex items-center gap-2 border-b border-line px-3.5 py-2.5">
        <div className="ui-input-group flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-line bg-raised/60 px-2.5 py-1">
          <Search className="h-4 w-4 shrink-0 text-faint" />
          <input data-command-search
            autoFocus
            role="combobox"
            aria-label={t("ui.components.overlays.CommandPalette.a72832b006c1")}
            aria-expanded={results.length > 0}
            aria-autocomplete="list"
            aria-controls={`${formId}-results`}
            aria-activedescendant={results[cursor] ? optionId(results[cursor].id) : undefined}
            aria-describedby={`${formId}-count`}
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActiveId(null) }}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229 || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return
              if (!results.length) return
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setActiveId(results[(cursor + 1) % results.length].id)
              } else if (e.key === 'ArrowUp') {
                e.preventDefault()
                setActiveId(results[(cursor - 1 + results.length) % results.length].id)
              } else if (e.key === 'Enter') {
                e.preventDefault()
                runAt(cursor)
              }
            }}
            placeholder={t("ui.components.overlays.CommandPalette.c869118b11e2")}
            className="h-7 min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
        </div>
        <Kbd>Esc</Kbd>
      </div>

      <div ref={listRef} className="scroll-fade-y no-scrollbar max-h-search overflow-y-auto py-1.5">
        {results.length === 0 ? (
          <>
            <ul id={`${formId}-results`} role="listbox" aria-label={t("ui.components.overlays.CommandPalette.a9f18651ec8b")} hidden />
            <p className="px-4 py-8 text-center text-xs text-faint">{t("ui.components.overlays.CommandPalette.8151e8704a7c")}</p>
          </>
        ) : (
          <ul id={`${formId}-results`} role="listbox" aria-label={t("ui.components.overlays.CommandPalette.a9f18651ec8b")}>
            {results.map((command, index) => (
              <li key={command.id} role="presentation">
                <button
                  type="button"
                  role="option"
                  tabIndex={-1}
                  id={optionId(command.id)}
                  aria-selected={index === cursor}
                  data-active={index === cursor}
                  onMouseEnter={() => setActiveId(command.id)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => runAt(index)}
                  className={cn(
                    'flex w-full items-center gap-2.5 px-3.5 py-1.5 text-left text-xs transition-colors',
                    index === cursor ? 'bg-brand/10 text-ink' : 'text-muted',
                  )}
                >
                  <span className="shrink-0 text-faint">{command.icon}</span>
                  <span className="min-w-0 flex-1 truncate">{command.title}</span>
                  {command.hint ? (
                    <span className="max-w-project shrink-0 truncate font-mono text-2xs text-faint">
                      {command.hint}
                    </span>
                  ) : null}
                  <Badge tone="neutral">{command.group}</Badge>
                  {index === cursor ? <CornerDownLeft className="h-3 w-3 shrink-0 text-brand" /> : null}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <p id={`${formId}-count`} role="status" className="border-t border-line px-3.5 py-2 text-2xs text-muted">{results.length ? t("ui.components.overlays.CommandPalette.d81189006def", {p0: (results.length), p1: (results.length === 40 ? t("ui.components.overlays.CommandPalette.6f89b4057391") : '')}) : t("ui.components.overlays.CommandPalette.3a4d4de450c4")}</p>
      {statusRoot === root && fileLoading ? <p role="status" className="px-3.5 pb-2 text-2xs text-muted">{t("ui.components.overlays.CommandPalette.1cc4614276fe")}</p> : null}
      {statusRoot === root && fileError ? <div role="alert" aria-label={t("ui.components.overlays.CommandPalette.1942bb02c037")} className="space-y-1 px-3.5 pb-2 text-2xs [overflow-wrap:anywhere]">
        <p tabIndex={0} className="max-h-16 overflow-auto text-muted">{noticeText(fileError)}</p>
        <button type="button" className="text-brand underline" onClick={() => setFileRetry((value) => value + 1)}>{t("ui.components.overlays.CommandPalette.2564b85e3440")}</button>
      </div> : null}
    </Overlay>
  )
}

/* ============================ 快捷键 ============================ */

export function ShortcutsDialog() {
  useLocale()
  const headingId = useId()

  const open = useAppStore((s) => s.shortcutsOpen)
  const setOpen = useAppStore((s) => s.setShortcutsOpen)

  if (!open) return null

  return (
    <Overlay labelledBy={headingId} onClose={() => setOpen(false)} className="flex max-h-dialog max-w-lg flex-col">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 id={headingId} className="ui-section-title">{t("ui.components.overlays.CommandPalette.89ce481a4b23")}</h2>
        <span className="ml-auto text-2xs text-faint">{isMac() ? 'macOS' : 'Windows / Linux'}</span>
        <IconButton label={t('ui.shortcuts.close')} onClick={() => setOpen(false)}><X className="h-4 w-4" /></IconButton>
      </div>
      <ul tabIndex={0} aria-label={t("ui.components.overlays.CommandPalette.89ce481a4b23")} className="min-h-0 overflow-y-auto divide-y divide-line">
        {HOTKEY_ROWS.map(({label: name, keys, group: desc}) => (
          <li key={name} className="flex items-center gap-3 px-4 py-2">
            <Check className={cn('h-3.5 w-3.5 shrink-0 text-transparent')} />
            <span className="min-w-0 flex-1">
              <span className="block text-xs text-ink">{name}</span>
              <span className="block text-2xs text-faint">{desc}</span>
            </span>
            <span className="flex shrink-0 gap-1">
              {formatHotkey(keys).split('+').map((key, index) => (
                <Kbd key={index}>{key}</Kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Overlay>
  )
}

export {Toaster} from './Toaster'
