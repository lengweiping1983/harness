import type { ResourceText } from '@/types'
import { noticeText } from '@/lib/noticeText'
import { t, useLocale } from '@/i18n'
import { useEffect, useRef, useState } from 'react'
import { ChevronDown, Download, FolderOpen, Loader2, Terminal } from 'lucide-react'
import { api } from '@/lib/api'
import { fileUrl } from '@/lib/fileLinks'
import { ContextActionMenu, type ContextMenuAnchor } from '@/components/ui/ContextActionMenu'

type SavePickerWindow = Window & {
  showSaveFilePicker?: (options: { suggestedName: string }) => Promise<FileSystemFileHandle>
}

/** Explicit desktop actions retain the preview's source workspace. */
export function FileOpenActions({ path, root }: { path: string; root: string | null }) {
  return <FileOpenActionsInstance key={JSON.stringify([root, path])} path={path} root={root} />
}

function FileOpenActionsInstance({ path, root }: { path: string; root: string | null }) {
  useLocale()

  const [anchor, setAnchor] = useState<ContextMenuAnchor | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ResourceText>('')
  const lock = useRef(false)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const name = path.split(/[\\/]/).pop() ?? path

  async function open(action: 'default' | 'terminal' | 'folder') {
    if (lock.current) return
    lock.current = true
    setBusy(true); setError('')
    try {
      const result = await api.openFile(path, root, action)
      if (mounted.current && !result.ok) setError(result.copy ?? result.message)
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  async function saveAs() {
    if (lock.current) return
    lock.current = true
    setBusy(true); setError('')
    try {
      const picker = (window as SavePickerWindow).showSaveFilePicker
      if (picker) {
        const handle = await picker.call(window, { suggestedName: name })
        const response = await fetch(fileUrl(path, { root, download: true }), { cache: 'no-store' })
        if (!response.ok || !response.body) { if (mounted.current) setError({key: 'ui.components.inspector.FileOpenActions.25603e3d9d18', params: {p0: response.status}}); return }
        const destination = await handle.createWritable()
        await response.body.pipeTo(destination)
      } else {
        const link = document.createElement('a')
        link.href = fileUrl(path, { root, download: true }); link.download = name
        document.body.append(link); link.click(); link.remove()
      }
    } catch (failure) {
      if (mounted.current && !(failure instanceof DOMException && failure.name === 'AbortError')) setError(failure instanceof Error ? failure.message : {key: 'ui.components.inspector.FileOpenActions.1f7b27f2263b'})
    } finally { lock.current = false; if (mounted.current) setBusy(false) }
  }
  function showMenu(origin: HTMLButtonElement, initial: 'first' | 'last' = 'first') {
    const rect = origin.getBoundingClientRect()
    setAnchor({ id: path, x: rect.right - 208, y: rect.bottom + 6, origin, initial })
  }

  return <div className="file-open-actions">
    <div className="flex shrink-0 items-center gap-1">
      <button type="button" aria-label={t("ui.components.inspector.FileOpenActions.064fc3c848b0")} title={t("ui.components.inspector.FileOpenActions.064fc3c848b0")} disabled={busy} onClick={() => void open('folder')} className="file-open-folder"><FolderOpen aria-hidden className="h-3.5 w-3.5" /></button>
      <div className="file-open-split">
        <button type="button" aria-label={t("ui.components.inspector.FileOpenActions.e7d96998577f")} title={t("ui.components.inspector.FileOpenActions.e7d96998577f")} disabled={busy} onClick={() => void open('default')} className="file-open-main">
          {busy ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <FolderOpen aria-hidden className="h-3.5 w-3.5" />}{t("ui.components.inspector.FileOpenActions.c771248e511f")}</button>
        <button type="button" aria-label={t("ui.components.inspector.FileOpenActions.726845b0a5c4")} aria-haspopup="menu" aria-expanded={Boolean(anchor)} disabled={busy} className="file-open-menu" onClick={(event) => anchor ? setAnchor(null) : showMenu(event.currentTarget)} onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); showMenu(event.currentTarget, event.key === 'ArrowUp' ? 'last' : 'first') }
        }}><ChevronDown aria-hidden className="h-3.5 w-3.5" /></button>
      </div>
    </div>
    {error ? <p role="alert" className="file-open-error">{noticeText(error)}</p> : null}
    {anchor ? <ContextActionMenu anchor={anchor} label={t("ui.components.inspector.FileOpenActions.2bab2821d17e")} onClose={() => setAnchor(null)} items={[
      { id: 'default-app', label: t('files.open.default_app'), icon: <FolderOpen aria-hidden className="h-4 w-4" />, run: () => void open('default') },
      { id: 'terminal', label: t('files.open.terminal'), icon: <Terminal aria-hidden className="h-4 w-4" />, run: () => void open('terminal') },
      { id: "folder", label: t("ui.components.inspector.FileOpenActions.064fc3c848b0"), icon: <FolderOpen aria-hidden className="h-4 w-4" />, separatorBefore: true, run: () => void open('folder') },
      { id: "save-as", label: t("ui.components.inspector.FileOpenActions.9016c463977d"), icon: <Download aria-hidden className="h-4 w-4" />, run: () => void saveAs() },
    ]} /> : null}
  </div>
}
