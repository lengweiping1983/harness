import { ResourceError } from './resourceError'
import { t } from '@/i18n'
import type { FileSearchResult } from '@/lib/protocol.generated'
import { runtimeRequest } from './http'
import type { TriggerCandidate, ScoredCandidate } from './trigger'

export function fileSearchPath(sessionId: string, query: string, refresh = false): string {
  const params = new URLSearchParams({ q: query, limit: '100', ...(refresh ? { refresh: '1' } : {}) })
  return `/sessions/${encodeURIComponent(sessionId)}/files/search?${params}`
}

export function referenceLiteral(absolute: string): string {
  return /[\s"'`\\]/.test(absolute) ? JSON.stringify(absolute).replace(/`/g, '\\u0060') : absolute
}

export function fileReferenceCandidates(value: unknown, sessionId: string, query: string, expectedRoot?: string | null): { result: FileSearchResult; candidates: TriggerCandidate[] } {
  if (!value || typeof value !== 'object') throw new ResourceError({key: "ui.lib.fileReferences.8f61881b9f28"})
  const result = value as FileSearchResult
  if (result.session_id !== sessionId || result.query !== query || typeof result.root !== 'string' || !result.root ||
      expectedRoot && result.root !== expectedRoot || !Array.isArray(result.matches) || result.matches.length > 100 ||
      typeof result.has_more !== 'boolean' || typeof result.truncated !== 'boolean' || !['git', 'filesystem'].includes(result.source)) throw new ResourceError({key: "ui.lib.fileReferences.4afa7f88ef21"})
  const windows = !result.root.startsWith('/')
  const normalize = (path: string) => windows ? path.replace(/\\/g, '/').toLowerCase() : path
  const base = normalize(result.root).replace(/\/$/, '')
  const seen = new Set<string>()
  const candidates = result.matches.map((item) => {
    if (!item || typeof item.path !== 'string' || typeof item.absolute_path !== 'string' || !['file', 'directory'].includes(item.kind) ||
        !item.path || item.path.length > 4096 || item.path.split('/').some((part) => !part || part === '.' || part === '..') ||
        /[\x00-\x1f]/.test(item.path) || normalize(item.absolute_path) !== base + '/' + normalize(item.path)) throw new ResourceError({key: "ui.lib.fileReferences.0df889a6d933"})
    const key = item.kind + ':' + item.path
    if (seen.has(key)) throw new ResourceError({key: "ui.lib.fileReferences.0937e6c4b745"})
    seen.add(key)
    return { kind: item.kind, title: item.path.split('/').at(-1)!, hint: item.path, insert: referenceLiteral(item.absolute_path) }
  })
  return { result, candidates }
}

export async function loadFileReferences(sessionId: string, query: string, expectedRoot: string | null | undefined, signal: AbortSignal, refresh = false) {
  const value = await runtimeRequest<unknown>(fileSearchPath(sessionId, query, refresh), 'GET', undefined, { signal })
  signal.throwIfAborted()
  return fileReferenceCandidates(value, sessionId, query, expectedRoot)
}

export function candidateKey(candidate: TriggerCandidate): string {
  return JSON.stringify([candidate.kind, candidate.insert])
}

export function selectedCandidateIndex(items: ScoredCandidate[], key: string | null): number {
  return Math.max(0, key ? items.findIndex((item) => candidateKey(item.candidate) === key) : 0)
}

/** Resolve the whole directory status; punctuation and word order belong to resources. */
export function fileCatalogStatus(result: Pick<FileSearchResult, 'source' | 'has_more' | 'truncated'>): string {
  if (result.source === 'git') {
    if (result.has_more) return t(result.truncated ? 'ui.composerFeedback.gitMoreTruncated' : 'ui.composerFeedback.gitMore')
    return t(result.truncated ? 'ui.composerFeedback.gitTruncated' : 'ui.composerFeedback.git')
  }
  if (result.has_more) return t(result.truncated ? 'ui.composerFeedback.filesMoreTruncated' : 'ui.composerFeedback.filesMore')
  return t(result.truncated ? 'ui.composerFeedback.filesTruncated' : 'ui.composerFeedback.files')
}
