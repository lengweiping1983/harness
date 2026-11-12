import { useMemo, useRef, useState } from 'react'
import { AlertTriangle, ChevronRight, Cpu, FileCode, Globe, History, ListTodo, Loader2, MessageCircleQuestion, Plug, Sparkles, Target, Terminal, Users, Wrench } from 'lucide-react'
import { t, useLocale } from '@/i18n'
import type { ToolSchema } from '@/types'
import { useAppStore } from '@/store/app'
import { Button, EmptyHint } from '@/components/ui/primitives'
import { WebSearchSettings } from './WebSearchSettings'

/* ============================ Tools ============================ */

/**
 * 分类 id -> 图标。id 与后端 `harness/tools/categories.py` 一一对应，
 * 前端只认 id；展示文案用后端给的 `category_label`，避免中英文两处各写一遍。
 */
const CATEGORY_ICON: Record<string, typeof Wrench> = {
  file: FileCode,
  shell: Terminal,
  terminal: Terminal,
  web: Globe,
  session: History,
  agent: Users,
  job: Loader2,
  plan: ListTodo,
  goal: Target,
  todo: ListTodo,
  skill: Sparkles,
  mcp: Plug,
  interaction: MessageCircleQuestion,
  system: Cpu,
  other: Wrench,
}

/** 分组排序用的缺省顺序；后端没给的分类排到最后。 */
const CATEGORY_ORDER = Object.keys(CATEGORY_ICON)

function categoryRank(id: string): number {
  const index = CATEGORY_ORDER.indexOf(id)
  return index === -1 ? CATEGORY_ORDER.length : index
}

export function BuiltinToolsPage() {
  const locale = useLocale()

  const tools = useAppStore((s) => s.tools)
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  const grouped = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = q
      ? tools.filter((t) => (t.name + ' ' + (t.description ?? '') + ' ' + (t.category_label ?? '')).toLowerCase().includes(q))
      : tools
    const map = new Map<string, { label: string; tools: ToolSchema[] }>()
    for (const tool of list) {
      const id = tool.category || 'other'
      const bucket = map.get(id)
      if (bucket) bucket.tools.push(tool)
      // 分类文案优先用后端给的（跟随 harness 自己的语言），前端不写死。
      else map.set(id, { label: tool.category_label || id, tools: [tool] })
    }
    // 先按后端声明的"常用度"排序，同一档内多的排前面。
    return [...map.entries()]
      .map(([id, bucket]) => ({ id, ...bucket }))
      .sort((a, b) => categoryRank(a.id) - categoryRank(b.id) || b.tools.length - a.tools.length)
  }, [tools, query, locale])
  const matching = grouped.reduce((count, group) => count + group.tools.length, 0)
  const clearSearch = () => {setQuery(''); searchRef.current?.focus()}

  return (
    <div className="space-y-5">
      <WebSearchSettings />
      <section aria-label={t('ui.settings.tools.title')} className="min-w-0 space-y-3">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="ui-section-title">{t('ui.settings.tools.title')}</h3>
          {tools.length > 0 && <p role="status" className="text-xs tabular-nums text-muted">{t(query.trim() ? 'ui.settings.tools.matches' : tools.length === 1 ? 'ui.settings.tools.totalOne' : 'ui.settings.tools.total', {count: matching, total: tools.length})}</p>}
        </header>
        <div className="flex min-w-0 items-center gap-2">
          <input
            ref={searchRef}
            type="search"
            aria-label={t('ui.settings.tools.search')}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('ui.settings.tools.searchHint')}
            className="ui-field min-w-0 flex-1 placeholder:text-faint focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand"
          />
          {query && <Button size="sm" onClick={clearSearch}>{t('ui.settings.tools.clear')}</Button>}
        </div>
        {grouped.length === 0 ? (
          <EmptyHint title={t(query.trim() ? 'ui.components.inspector.Inspector.2d88d6c2bbbf' : 'ui.settings.tools.empty')} description={t(query.trim() ? 'ui.settings.tools.noMatchHint' : 'ui.settings.tools.emptyHint')} />
        ) : (
          <div className="space-y-3">
            {grouped.map((group) => {
              const Icon = CATEGORY_ICON[group.id] ?? Wrench
              return (
                <div key={group.id}>
                  <h4 className="ui-group-title mb-1.5 flex items-center gap-2 text-muted">
                    <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />
                    <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{group.label}</span>
                    <span className="shrink-0 tabular-nums">{t(group.tools.length === 1 ? 'ui.settings.tools.totalOne' : 'ui.settings.tools.total', {count: group.tools.length})}</span>
                  </h4>
                  <ul className="space-y-px">
                    {group.tools.map((tool) => (
                      <li key={tool.name}>
                        <details className="group/tool rounded-lg border border-transparent open:border-line">
                          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-2 py-2 text-xs text-muted transition-colors hover:bg-raised hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand [&::-webkit-details-marker]:hidden">
                            <ChevronRight aria-hidden className="h-3 w-3 shrink-0 transition-transform group-open/tool:rotate-90" />
                            <code className="min-w-0 break-all font-mono text-brand/90">{tool.name}</code>
                            {tool.dangerous ? (
                              <span role="img" aria-label={t("ui.components.inspector.Inspector.e0f7d43c252f")} className="shrink-0"><AlertTriangle aria-hidden className="h-3 w-3 text-warn" /></span>
                            ) : null}
                          </summary>
                          <p className="whitespace-pre-wrap [overflow-wrap:anywhere] border-t border-line px-3 py-2 text-xs leading-relaxed text-muted">{tool.description || t("ui.components.inspector.Inspector.0042f1887704")}</p>
                        </details>
                      </li>
                    ))}
                  </ul>
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
