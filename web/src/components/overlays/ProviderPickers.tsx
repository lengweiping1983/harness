import {useMemo, useState} from 'react'
import {Search} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {Button, Overlay} from '@/components/ui/primitives'
import type {CatalogModel, ProviderModel} from '@/lib/providerConnections'
import {addProviderModels, latestProviderModels} from '@/lib/providerConnections'

const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-xs'
const catalogPageSize = 50
const providerPickerTitleId = 'provider-picker-title'
const modelPickerTitleId = 'model-picker-title'

export type ProviderChoice = {id: string; name: string; keywords: string}

/** 供应商目录弹层：只回答“用谁家”，选中后关闭，由调用方展开该供应商的编辑表单。 */
export function ProviderPicker({choices, onPick, onClose}: {choices: ProviderChoice[]; onPick: (id: string) => void; onClose: () => void}) {
  useLocale()
  const [query, setQuery] = useState('')
  const filtered = choices.filter(row => `${row.name} ${row.id} ${row.keywords}`.toLowerCase().includes(query.trim().toLowerCase()))
  return <Overlay labelledBy={providerPickerTitleId} onClose={onClose} className="flex h-picker w-full max-w-xl flex-col p-0">
    <header className="space-y-2 border-b border-line px-4 py-3">
      <h3 id={providerPickerTitleId} className="ui-section-title">{t('ui.providers.chooseProviderTitle')}</h3>
      <p className="text-xs text-muted">{t('ui.providers.chooseProviderHint')}</p>
      <label className="flex items-center gap-2"><Search aria-hidden className="h-4 w-4 shrink-0 text-muted" /><input className={field} aria-label={t('ui.providers.search')} placeholder={t('ui.providers.search')} autoComplete="off" value={query} onChange={event => setQuery(event.target.value)} /></label>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      {filtered.length ? <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{filtered.map(row => <Button key={row.id} size="sm" className="justify-start" onClick={() => onPick(row.id)}>{row.name}</Button>)}</div>
        : <p role="status" className="py-3 text-xs text-muted">{t('ui.providers.noProviders')}</p>}
    </div>
    <footer className="flex justify-end border-t border-line px-4 py-3"><Button size="sm" onClick={onClose}>{t('ui.providers.cancel')}</Button></footer>
  </Overlay>
}

/** 模型列表弹层：候选模型多，独立成屏；勾选只在弹层内生效，确定后才写回接口草稿。 */
export function ModelPicker({context, available, catalog, selected, onConfirm, onClose}: {
  context: string
  available: CatalogModel[]
  catalog: readonly CatalogModel[]
  selected: ProviderModel[]
  onConfirm: (models: ProviderModel[]) => void
  onClose: () => void
}) {
  useLocale()
  const [draft, setDraft] = useState(selected)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const selectedIds = useMemo(() => new Set(draft.map(model => model.model)), [draft])
  const filtered = available.filter(row => `${row.id} ${row.name}`.toLowerCase().includes(query.trim().toLowerCase()))
  const latest = useMemo(() => latestProviderModels(available), [available])
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / catalogPageSize) - 1))
  const matchingSelected = filtered.filter(row => selectedIds.has(row.id)).length
  const matchingUnselected = filtered.length - matchingSelected
  return <Overlay labelledBy={modelPickerTitleId} onClose={onClose} className="flex h-picker w-full max-w-2xl flex-col p-0">
    <header className="space-y-2 border-b border-line px-4 py-3">
      <h3 id={modelPickerTitleId} className="ui-section-title">{t('ui.providers.modelPickerTitle')}</h3>
      {context ? <p className="text-xs text-muted">{context}</p> : null}
      <label className="flex items-center gap-2"><Search aria-hidden className="h-4 w-4 shrink-0 text-muted" /><input className={field} aria-label={t('ui.providers.searchModels')} placeholder={t('ui.providers.searchModels')} autoComplete="off" value={query} onChange={event => {setQuery(event.target.value); setPage(0)}} /></label>
      <div className="flex flex-wrap items-center gap-2">
        <span role="status" className="text-xs text-muted">{t('ui.providers.matchingModels', {count: filtered.length})}</span>
        <Button size="xs" variant="ghost" disabled={!matchingUnselected} onClick={() => setDraft(addProviderModels(draft, filtered, catalog))}>{t('ui.providers.selectAllCount', {count: filtered.length})}</Button>
        <Button size="xs" variant="ghost" disabled={!matchingSelected} onClick={() => {const ids = new Set(filtered.map(row => row.id)); setDraft(draft.filter(model => !ids.has(model.model)))}}>{t('ui.providers.clearCount', {count: filtered.length})}</Button>
        <Button size="xs" variant="ghost" disabled={!latest.some(row => !selectedIds.has(row.id))} onClick={() => setDraft(addProviderModels(draft, latest, catalog))}>{t('ui.providers.addLatest')}</Button>
      </div>
    </header>
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <div role="group" aria-label={t('ui.providers.modelPickerTitle')} className="space-y-1">{filtered.slice(currentPage * catalogPageSize, (currentPage + 1) * catalogPageSize).map(row => <label key={row.id} className="flex cursor-pointer items-start gap-2 rounded-lg px-2 py-2 text-xs hover:bg-canvas"><input type="checkbox" checked={selectedIds.has(row.id)} onChange={event => setDraft(event.target.checked ? addProviderModels(draft, [row], catalog) : draft.filter(model => model.model !== row.id))} /><span className="min-w-0 break-all"><span className="block">{row.name || row.id}</span>{row.name !== row.id ? <span className="block text-muted">{row.id}</span> : null}</span></label>)}</div>
      {!filtered.length ? <p role="status" className="py-3 text-xs text-muted">{t('ui.providers.noModels')}</p> : null}
      <ModelPagination label={t('ui.providers.modelPickerTitle')} total={filtered.length} page={currentPage} size={catalogPageSize} onPage={setPage} />
    </div>
    <footer className="border-t border-line px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span role="status" className="text-xs text-muted">{t('ui.providers.selectedModels', {count: draft.length})}</span>
        <div className="flex gap-2"><Button size="sm" onClick={onClose}>{t('ui.providers.cancel')}</Button><Button size="sm" variant="primary" onClick={() => onConfirm(draft)}>{t('ui.providers.confirm')}</Button></div>
      </div>
    </footer>
  </Overlay>
}

export function ModelPagination({label, total, page, size, onPage}: {label: string; total: number; page: number; size: number; onPage: (page: number) => void}) {
  if (total <= size) return null
  return <nav aria-label={label} className="mt-2 flex flex-wrap items-center justify-between gap-2">
    <span role="status" className="text-xs text-muted">{t('ui.providers.modelPage', {start: page * size + 1, end: Math.min((page + 1) * size, total), total})}</span>
    <div className="flex gap-2"><Button size="sm" disabled={page === 0} onClick={() => onPage(page - 1)}>{t('ui.providers.previousPage')}</Button><Button size="sm" disabled={(page + 1) * size >= total} onClick={() => onPage(page + 1)}>{t('ui.providers.nextPage')}</Button></div>
  </nav>
}
