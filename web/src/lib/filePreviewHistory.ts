import type { FileLineRange } from './fileNavigation'

export interface FilePreviewReference {
  path: string
  root: string | null
  lineRange?: FileLineRange
}

export const filePreviewScope = (sessionId: string | null) => JSON.stringify([sessionId])
export const EMPTY_FILE_HISTORY: FilePreviewReference[] = []

function referencePath(value: string, windows: boolean): string {
  const unified = value.replace(/\\/g, '/').replace(/\/+$/, '')
  return windows ? unified.toLowerCase() : unified
}

export function sameFileReference(a: FilePreviewReference, b: FilePreviewReference): boolean {
  const windows = /^[A-Za-z]:[\\/]/.test(a.root ?? a.path)
  return referencePath(a.path, windows) === referencePath(b.path, windows)
    && (a.root === null ? b.root === null : b.root !== null && referencePath(a.root, windows) === referencePath(b.root, windows))
    && a.lineRange?.start === b.lineRange?.start && a.lineRange?.end === b.lineRange?.end
}

/** Bounded per-chat tabs; revisiting a citation keeps its position and source. */
export function rememberFilePreview(
  histories: Record<string, FilePreviewReference[]>,
  sessionId: string | null,
  reference: FilePreviewReference,
): Record<string, FilePreviewReference[]> {
  const scope = filePreviewScope(sessionId)
  const current = histories[scope] ?? []
  const entries = current.some((item) => sameFileReference(item, reference))
    ? current
    : [...current, reference].slice(-12)
  // Property insertion order tracks recently visited chats, keeping memory bounded.
  const other = Object.entries(histories).filter(([key]) => key !== scope).slice(-31)
  return Object.fromEntries([...other, [scope, entries]])
}

/** Select the neighbour to the right, falling back to the previous tab. */
export function closeFilePreview(entries: FilePreviewReference[], reference: FilePreviewReference) {
  const index = entries.findIndex((item) => sameFileReference(item, reference))
  if (index < 0) return { entries, next: undefined }
  const remaining = entries.filter((_, position) => position !== index)
  return { entries: remaining, next: remaining[index] ?? remaining[index - 1] }
}
