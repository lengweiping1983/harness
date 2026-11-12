import type {ResourceText} from '@/types'
import {ApiError, isUnconfirmedWrite, runtimeRequest} from './http'
import {failureText, ResourceError} from './resourceError'

export type GitRepository = {root: string; branch: string | null; refs: string[]; head: string | null; index_revision: string; conflicts: boolean; staged_files: Array<{path: string; status: string}>; files: Array<{path: string; status: string; original_path?: string}>}
export type GitPatch = {path: string; diff: string; binary: boolean; truncated: boolean}
type WriteResult = {confirmed: boolean; repository?: GitRepository; commit?: string; error?: ResourceText}
const errorKeys = new Map([
  ['git/index-changed', 'ui.git.error.indexChanged'], ['git/file-changed', 'ui.git.error.fileChanged'],
  ['git/invalid', 'ui.git.error.invalid'], ['git/conflicts', 'ui.git.error.conflicts'],
  ['git/empty-index', 'ui.git.error.emptyIndex'], ['git/commit-uncertain', 'ui.git.error.commitUncertain'],
  ['git/directory-changed', 'ui.git.error.directoryChanged'], ['git/not-repository', 'ui.git.error.notRepository'],
  ['git/metadata-present', 'ui.git.error.metadataPresent'], ['git/environment-override', 'ui.git.error.environmentOverride'],
])

export function gitFailureText(failure: unknown): ResourceText {
  const key = failure instanceof ApiError ? errorKeys.get(failure.code) : undefined
  return key ? {key} : failureText(failure)
}

const pending = new Map<string, Promise<WriteResult>>()
const invalid = () => new ResourceError({key: 'ui.http.invalidResponse'})

function repository(value: unknown, root: string): GitRepository {
  const row = value as Partial<GitRepository> | null
  const files = (value: unknown) => Array.isArray(value) && value.every(item => item && typeof item === 'object' && typeof item.path === 'string' && typeof item.status === 'string' && (item.original_path === undefined || typeof item.original_path === 'string'))
  if (!row || row.root !== root || typeof row.index_revision !== 'string' || !row.index_revision ||
      !(row.branch === null || typeof row.branch === 'string') || !(row.head === null || typeof row.head === 'string') ||
      typeof row.conflicts !== 'boolean' || !Array.isArray(row.refs) || !row.refs.every(ref => typeof ref === 'string') || !files(row.files) || !files(row.staged_files)) throw invalid()
  return row as GitRepository
}

export async function readGitRepository(base: string, root: string, signal?: AbortSignal) {
  return repository(await runtimeRequest<unknown>(base, 'GET', undefined, {signal}), root)
}

export async function readGitPatch(base: string, path: string, revision: string, signal: AbortSignal): Promise<GitPatch> {
  const value = await runtimeRequest<GitPatch>(base + '/staged?path=' + encodeURIComponent(path) + '&expected_revision=' + encodeURIComponent(revision), 'GET', undefined, {signal})
  if (!value || value.path !== path || typeof value.diff !== 'string' || typeof value.binary !== 'boolean' || typeof value.truncated !== 'boolean') throw invalid()
  return value
}

/** 同一仓库的受理写入与核实读取共享归属，关闭面板不释放在途操作。 */
export function writeGitOnce(base: string, root: string, action: string, payload: Record<string, unknown>) {
  const current = pending.get(root)
  if (current) return {started: false, result: current}
  const result = Promise.resolve().then(async (): Promise<WriteResult> => {
    try {
      const value = await runtimeRequest<unknown>(base + '/' + action, 'POST', payload)
      if (action === 'commit') {
        const commit = value as {commit?: unknown; repository?: unknown} | null
        if (!commit || typeof commit.commit !== 'string' || !/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(commit.commit)) throw invalid()
        const saved = repository(commit.repository, root)
        return {confirmed: true, repository: saved, commit: commit.commit}
      }
      return {confirmed: true, repository: repository(value, root)}
    } catch (failure) {
      if (failure instanceof ApiError && failure.code !== 'git/commit-uncertain' && !isUnconfirmedWrite(failure.failure)) return {confirmed: false, error: gitFailureText(failure)}
      const reason = gitFailureText(failure)
      try {
        const saved = await readGitRepository(base, root)
        return {confirmed: false, repository: saved, error: {template: {key: 'ui.git.operationUnconfirmed', params: {reason}}}}
      } catch (check) {
        return {confirmed: false, error: {template: {key: 'ui.git.operationUnconfirmedReadFailed', params: {reason, check: gitFailureText(check)}}}}
      }
    }
  }).finally(() => { if (pending.get(root) === result) pending.delete(root) })
  pending.set(root, result)
  return {started: true, result}
}
