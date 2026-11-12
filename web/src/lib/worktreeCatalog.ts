import type { SessionSummary } from '@/types'
import { normalizePath, sessionDirectory } from './workspaces'

export type ManagedWorktree = { id: string; repo: string; path: string; branch?: string | null; status: string }
export type WorktreeStatusFilter = 'all' | 'active' | 'archived'

function contains(directory: string, path: string): boolean {
  const root = normalizePath(directory), candidate = normalizePath(path)
  return Boolean(root && candidate && (candidate === root || candidate.startsWith(root.endsWith('/') ? root : root + '/')))
}

export function worktreeCatalog(rows: ManagedWorktree[], sessions: SessionSummary[], options: {
  cwd?: string | null; repositoryRoot?: string | null; query?: string; status?: WorktreeStatusFilter; currentProjectOnly?: boolean
}) {
  const cwd = options.cwd ?? ''
  const currentTree = rows.filter(row => contains(row.path, cwd)).sort((a, b) => normalizePath(b.path).length - normalizePath(a.path).length)[0]
  const project = normalizePath(currentTree?.repo ?? options.repositoryRoot)
  const terms = (options.query ?? '').trim().toLocaleLowerCase().split(/\s+/).filter(Boolean)
  return rows.map(row => {
    const chat = sessions.find(session => normalizePath(sessionDirectory(session)) === normalizePath(row.path))
    return { row, chat, current: contains(row.path, cwd) }
  }).filter(({ row, chat }) => {
    if (options.status && options.status !== 'all' && row.status !== options.status) return false
    if (options.currentProjectOnly && (!project || normalizePath(row.repo) !== project)) return false
    const text = [chat?.title, row.branch, row.repo, row.path].filter(Boolean).join('\n').toLocaleLowerCase()
    return terms.every(term => text.includes(term))
  }).sort((a, b) => Number(b.current) - Number(a.current) || Number(b.row.status === 'active') - Number(a.row.status === 'active'))
}
