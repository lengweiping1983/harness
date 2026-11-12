import { ResourceError } from './resourceError'
import type { Comparison, ComparisonFile, ComparisonOptions, ComparisonPatch, NetChangedFile } from './protocol.generated'
import type { ChangesTarget } from './turnChanges'
import { readTurnChanges } from './turnChanges'
import { appendNetChanges, readNetChanges, readNetFile } from './netChanges'
import { runtimeRequest, ApiError } from './http'
import { t } from '@/i18n'
import { relativizeToRoot } from './deliverables'

export type ComparisonScope = 'history' | 'uncommitted' | 'unstaged' | 'staged' | 'commit' | 'branch'
export type ComparisonQuery = { scope: ComparisonScope; base: string; target: string }
export type ChangeFile = Pick<ComparisonFile, 'path' | 'status' | 'added' | 'deleted'> & { old_path?: string | null; history?: NetChangedFile }
export type ChangeSnapshot = {
  files: ChangeFile[]; total: number; revision: string; left: string; right: string; root: string | null
  added: number | null; deleted: number | null; through?: number; historyTarget?: ChangesTarget; historyEnded?: boolean
}
const prefix = (id: string) => '/sessions/' + encodeURIComponent(id) + '/comparison'
const parameters = (query: ComparisonQuery) => new URLSearchParams({ scope: query.scope, base: query.base, target: query.target })
export function readComparisonOptions(id: string, search = '', offset = 0, signal?: AbortSignal) {
  return runtimeRequest<ComparisonOptions>(prefix(id) + '/options?' + new URLSearchParams({ search, offset: String(offset) }), 'GET', undefined, { signal })
}
export async function readComparison(id: string, query: ComparisonQuery, historyTarget: ChangesTarget | null, signal: AbortSignal): Promise<ChangeSnapshot> {
  if (query.scope === 'history') {
    if (!historyTarget) throw new ResourceError({key: 'ui.changes.noHistory'})
    const [first, evidence] = await Promise.all([readNetChanges(historyTarget, signal), readTurnChanges(historyTarget, signal)])
    let snapshot = first
    while (snapshot.next_offset !== null) {
      const next = await readNetChanges(historyTarget, signal, { offset: snapshot.next_offset, through: snapshot.through })
      snapshot = appendNetChanges(snapshot, next)
    }
    const files: ChangeFile[] = snapshot.files.filter(file => file.kind !== 'unchanged').map(file => ({
      path: file.path, status: file.kind === 'created' ? 'A' : file.available ? 'M' : '?',
      added: file.counts?.added ?? null, deleted: file.counts?.removed ?? null, history: file,
    })).sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0)
    return { files, total: files.length, revision: String(snapshot.through), through: snapshot.through, historyTarget, historyEnded: snapshot.ended,
      left: 'BEFORE', right: 'AFTER', root: evidence.workspace_root,
      added: files.every(f => f.added !== null) ? files.reduce((n, f) => n + f.added!, 0) : null,
      deleted: files.every(f => f.deleted !== null) ? files.reduce((n, f) => n + f.deleted!, 0) : null }
  }
  const params = parameters(query)
  const first = await runtimeRequest<Comparison>(prefix(id) + '?' + params, 'GET', undefined, { signal })
  const files = [...first.files]
  let offset = first.next_offset
  while (offset !== null) {
    params.set('offset', String(offset)); params.set('revision', first.revision)
    const next = await runtimeRequest<Comparison>(prefix(id) + '?' + params, 'GET', undefined, { signal })
    if (next.revision !== first.revision || next.total !== first.total || next.next_offset !== null && next.next_offset <= offset) {
      throw new ApiError({ok: false, message: t('ui.changes.changed'), copy: {key: 'ui.changes.changed'}, code: 'review/changed', status: 409})
    }
    files.push(...next.files); offset = next.next_offset
  }
  if (files.length !== first.total || new Set(files.map(f => f.path)).size !== files.length) throw new ResourceError({key: 'ui.changes.changed'})
  return { files, total: first.total, revision: first.revision, left: first.left, right: first.right, root: first.workspace, added: first.added, deleted: first.deleted }
}
export async function readComparisonPatch(id: string, query: ComparisonQuery, snapshot: ChangeSnapshot, file: ChangeFile, signal: AbortSignal): Promise<ComparisonPatch> {
  if (snapshot.historyTarget && file.history && snapshot.through !== undefined) {
    if (!file.history.available) throw new ResourceError({key: 'ui.changes.historyUnavailable'})
    const patch = await readNetFile(snapshot.historyTarget, file.history, snapshot.through, signal)
    return { path: file.path, patch: patch.patch ?? '', truncated: patch.truncated, binary: false, conflict: false }
  }
  const params = parameters(query)
  params.set('revision', snapshot.revision); params.set('path', file.path)
  const next = await runtimeRequest<Comparison>(prefix(id) + '?' + params, 'GET', undefined, { signal })
  if (next.revision !== snapshot.revision || next.selection?.path !== file.path) throw new ResourceError({key: 'ui.changes.changed'})
  return next.selection
}

export type ChangeTreeNode = { name: string; path: string; file?: ChangeFile; children: ChangeTreeNode[] }
export function changeTree(files: ChangeFile[], filter: string, root: string | null = null): ChangeTreeNode[] {
  const roots: ChangeTreeNode[] = []
  for (const file of files) {
    if (!file.path.toLocaleLowerCase().includes(filter.toLocaleLowerCase())) continue
    const parts = relativizeToRoot(file.path, root).split('/').filter(Boolean)
    let children = roots
    for (let index = 0; index < parts.length; index++) {
      const path = parts.slice(0, index + 1).join('/')
      let node = children.find(item => item.path === path)
      if (!node) { node = { name: parts[index], path, children: [] }; children.push(node) }
      if (index === parts.length - 1) node.file = file
      children = node.children
    }
  }
  return roots
}
