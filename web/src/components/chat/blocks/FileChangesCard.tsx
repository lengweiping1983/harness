import { t, useLocale } from '@/i18n'
import { memo, useState } from 'react'
import { cn } from '@/lib/utils'
import { FileDiff, ChevronRight, X } from 'lucide-react'
import { useChangeCards } from '@/store/changeCards'
import { Tooltip } from '@/components/ui/Tooltip'
import type { ChangedFile } from '@/lib/fileChanges'
import { useAppStore } from '@/store/app'
import { FileChip } from '@/components/markdown/Markdown'

export const FileChangesCard = memo(function FileChangesCard({ id, files, sessionId, turn, pending }: { id: string; files: ChangedFile[]; sessionId: string | null; turn?: number; pending: boolean }) {
  useLocale()

  const [expanded, setExpanded] = useState(false)
  const review = useAppStore((s) => s.setInspectorTab)
  const reveal = useAppStore((s) => s.revealTurnChanges)
  const counted = files.every((file) => file.added !== undefined && file.removed !== undefined)
  return <section aria-label={t("ui.components.chat.blocks.FileChangesCard.ef4686e72773")} data-changes-turn={turn} className="overflow-hidden rounded-xl border border-line text-xs">
    <header className="flex items-center gap-2 border-b border-line px-3 py-3">
      <FileDiff aria-hidden className="h-4 w-4 shrink-0 text-muted" />
      <div className="min-w-0 flex-1"><p className="font-medium text-ink">{t(files.length === 1 ? 'ui.fileChanges.modifiedOne' : 'ui.fileChanges.modifiedMany', {count: files.length})}</p>
        {counted ? <p className="mt-1" title={t("ui.components.chat.blocks.FileChangesCard.54346c09be1c")}><span className="text-ok">+{files.reduce((sum, file) => sum + file.added!, 0)}</span> <span className="text-danger">-{files.reduce((sum, file) => sum + file.removed!, 0)}</span></p> : null}
      </div>
      <button type="button" onClick={() => sessionId && turn ? reveal(sessionId, turn) : review('changes')} className="shrink-0 rounded-lg border border-line px-2 py-1.5 text-ink hover:bg-raised">{t('ui.changes.view')}</button>
      <Tooltip content={t("ui.components.chat.blocks.FileChangesCard.c3754380ca26")}>
        <button type="button" aria-label={t("ui.components.chat.blocks.FileChangesCard.7132ef48f98b")} onClick={() => {
          useChangeCards.getState().close(sessionId, { id, pending })
          useAppStore.getState().toast({ title: t("ui.components.chat.blocks.FileChangesCard.024174aee416"), description: t("ui.components.chat.blocks.FileChangesCard.6f1cd407d7ac"), tone: 'info' })
        }} className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-raised hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"><X aria-hidden className="h-4 w-4" /></button>
      </Tooltip>
    </header>
    <ul className="space-y-1 px-3 py-2">{(expanded ? files : files.slice(0, 3)).map((file) => <li key={file.path} className="flex min-w-0 items-center justify-between gap-2 py-1">
      <div className="min-w-0 truncate"><FileChip path={file.path} label={file.path} /></div>
      {file.added !== undefined ? <span className="shrink-0 tabular-nums"><span className="text-ok">+{file.added}</span> <span className="text-danger">-{file.removed}</span></span> : null}
    </li>)}</ul>
    {files.length > 3 ? <button type="button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)} className="flex items-center gap-1 px-3 pb-3 text-muted hover:text-ink">{expanded ? t("ui.components.chat.blocks.FileChangesCard.5864e2396e74") : t("ui.components.chat.blocks.FileChangesCard.336501c41f83", {p0: (files.length - 3)})}<ChevronRight aria-hidden className={cn('h-3 w-3 shrink-0', expanded && 'rotate-90')} /></button> : null}
    {pending ? <p className="px-3 pb-2 text-faint">{t("ui.components.chat.blocks.FileChangesCard.c6d87831488d")}</p> : null}
  </section>
})
