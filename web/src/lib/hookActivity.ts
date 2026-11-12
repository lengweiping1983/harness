import { t } from '@/i18n'
import type { HookActivity } from '@/types'

const events = new Set(['PreToolUse', 'PostToolUse', 'PostToolUseFailure', 'Stop'])
const statuses = new Set(['running', 'passed', 'blocked', 'failed', 'timed_out', 'cancelled', 'cleanup_failed', 'skipped', 'interrupted', 'stopped'])
export const hookPhases = { get PreToolUse() { return t("ui.lib.hookActivity.1e3232cb32f1") }, get PostToolUse() { return t("ui.lib.hookActivity.aeddb59f16a9") }, get PostToolUseFailure() { return t("ui.lib.hookActivity.d3fafd1aa70e") }, get Stop() { return t("ui.lib.hookActivity.b8b25c6d8362") } }
export const hookStates = { get running() { return t("ui.lib.hookActivity.1f0eb99b7ed0") }, get passed() { return t("ui.lib.hookActivity.3fa9b684903a") }, get blocked() { return t("ui.lib.projectHooks.61781abf9905") }, get failed() { return t("ui.lib.hookActivity.2875bc4f7fcb") }, get timed_out() { return t("ui.lib.hookActivity.73ce15fee42e") }, get cancelled() { return t("ui.components.chat.blocks.MessageBlocks.f006455e3baf") }, get cleanup_failed() { return t("ui.lib.hookActivity.8da0d7473372") }, get skipped() { return t("ui.lib.projectHooks.f95ea7f4c063") }, get interrupted() { return t("ui.lib.projectHooks.e6bedaccc265") }, get stopped() { return t("ui.components.chat.blocks.MessageBlocks.f006455e3baf") } }

export function readHookActivity(value: unknown): HookActivity | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  if (['id', 'root', 'session_id', 'call_id', 'tool', 'label', 'reason'].some((key) => typeof raw[key] !== 'string') || !raw.id || !raw.call_id ||
      !events.has(String(raw.event)) || !statuses.has(String(raw.status)) ||
      !Number.isSafeInteger(raw.source_seq) || Number(raw.source_seq) < 0 ||
      !Number.isSafeInteger(raw.turn) || Number(raw.turn) < 1 || !Number.isSafeInteger(raw.step) || Number(raw.step) < 0 ||
      typeof raw.started !== 'number' || !Number.isFinite(raw.started) ||
      typeof raw.duration_ms !== 'number' || !Number.isFinite(raw.duration_ms) || raw.duration_ms < 0) return null
  // Whitelist small fields: raw journal exports can also contain command and logs.
  return { id: String(raw.id), root: String(raw.root), session_id: String(raw.session_id), call_id: String(raw.call_id),
    tool: String(raw.tool), label: String(raw.label).slice(0, 160), reason: String(raw.reason).slice(0, 320),
    event: raw.event as HookActivity['event'], status: raw.status as HookActivity['status'],
    turn: Number(raw.turn), step: Number(raw.step), source_seq: Number(raw.source_seq), started: raw.started, duration_ms: raw.duration_ms,
    ...(typeof raw.finished === 'number' && Number.isFinite(raw.finished) ? { finished: raw.finished } : {}) }
}

export function mergeHookActivity(previous: HookActivity[] | undefined, incoming: HookActivity): HookActivity[] {
  const rows = previous ?? [], old = rows.find((row) => row.id === incoming.id)
  if (old && (incoming.source_seq <= old.source_seq || old.status !== 'running' && incoming.status === 'running')) return rows
  return old ? rows.map((row) => row.id === incoming.id ? incoming : row) : [...rows, incoming]
}

export function settleHookActivity(rows: HookActivity[] | undefined): HookActivity[] | undefined {
  return rows?.map((row) => row.status === 'running' ? { ...row, status: 'interrupted', reason: '', reasonCopy: {key: 'ui.lib.hookActivity.c28f6401fb6d'} } : row)
}

export function hookImpact(row: HookActivity): string {
  if (row.status === 'stopped') return t('ui.hooks.stoppedOutput')
  if (row.event === 'PreToolUse' && ['failed', 'timed_out'].includes(row.status)) return t('ui.hooks.failedWithoutDenial')
  if (row.event === 'Stop' && !['passed', 'skipped', 'cancelled', 'interrupted', 'cleanup_failed'].includes(row.status)) return row.status === 'running' ? t("ui.lib.hookActivity.ace42e793eae") : row.status === 'blocked' ? t("ui.lib.hookActivity.ba622e3251ec") : t("ui.lib.hookActivity.5be2a2f748cf")
  if (row.status === 'running') return row.event === 'PreToolUse' ? t("ui.lib.hookActivity.c3725381d08e") : row.event === 'PostToolUseFailure' ? t("ui.lib.hookActivity.7fe75337b4ea") : t("ui.lib.hookActivity.d68da4da595b")
  if (row.status === 'skipped') return t("ui.lib.hookActivity.fa053974ed15")
  if (row.status === 'passed') return ''
  if (row.status === 'cancelled' || row.status === 'interrupted' || row.status === 'cleanup_failed') return t("ui.lib.hookActivity.147aeeacdf85")
  return row.event === 'PreToolUse' ? t("ui.lib.hookActivity.4e0e9aa6c9d1") : row.event === 'PostToolUseFailure' ? t("ui.lib.hookActivity.6df16b460834") : t("ui.lib.hookActivity.d42452b5692f")
}

export function hookStateLabel(row: HookActivity): string {
  return row.status === 'blocked' && row.event !== 'PreToolUse' ? t("ui.lib.hookActivity.2875bc4f7fcb") : hookStates[row.status]
}

export function hookEvidencePath(sessionId: string, row: HookActivity): string {
  return `/sessions/${encodeURIComponent(sessionId)}/hooks/${encodeURIComponent(row.id)}?source_seq=${row.source_seq}`
}
