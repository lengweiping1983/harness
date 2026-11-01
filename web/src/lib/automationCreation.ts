import {newIdentity} from '@/lib/ids'
import type {Automation, AutomationPayload} from './automations'
import {ApiError, isUnconfirmedWrite, runtimeRequest} from './http'
import {ResourceError} from './resourceError'

type Creation = {creation_id: string; name: string; prompt: string; session_id: string; mode: string; schedule: unknown; timezone: string; enabled: boolean | number}
const active = new Set<string>()
const failure = (key = 'ui.automations.creationUnconfirmed') => new ResourceError({key})
const storageKey = (session: string) => 'harness:automation-creation:' + encodeURIComponent(session)

function sameBody(row: Automation, body: Creation): boolean {
  return row.id === 'auto_' + body.creation_id && row.session_id === body.session_id &&
    row.name === body.name.trim() && row.prompt === body.prompt.trim() && row.mode === body.mode &&
    row.timezone === body.timezone && row.enabled === Number(body.enabled) &&
    JSON.stringify(row.schedule) === JSON.stringify(body.schedule)
}

function pending(key: string, session: string): Creation | null {
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return null
    if (raw.length > 262144) throw failure()
    const row = JSON.parse(raw) as Creation | null
    if (!row || typeof row !== 'object' || Array.isArray(row) ||
        typeof row.creation_id !== 'string' || !/^[a-f0-9]{32}$/.test(row.creation_id) ||
        row.session_id !== session || typeof row.name !== 'string' || typeof row.prompt !== 'string' ||
        typeof row.mode !== 'string' || typeof row.timezone !== 'string' ||
        ![true, false, 0, 1].includes(row.enabled) || !row.schedule || typeof row.schedule !== 'object') throw failure()
    return row
  } catch { throw failure() }
}

function clear(key: string) {
  try { localStorage.removeItem(key) }
  catch { throw failure('ui.automations.creationReceiptCleanupFailed') }
}

async function confirm(key: string, body: Creation): Promise<void> {
  let catalog: AutomationPayload
  try { catalog = await runtimeRequest<AutomationPayload>('/automations') }
  catch { throw failure() }
  if (!Array.isArray(catalog?.automations) || catalog.automations.some(row => !row || typeof row.id !== 'string')) throw failure()
  const matches = catalog.automations.filter(row => row.id === 'auto_' + body.creation_id)
  if (!matches || matches.length !== 1 || !sameBody(matches[0], body)) throw failure()
  clear(key)
}

/** 受理前保存创建身份；未知结果只回查原身份，换页和刷新后均不重发。 */
export async function saveAutomationOnce(value: unknown): Promise<void> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw failure()
  const body = value as Record<string, unknown>
  if (body.id !== undefined) { await runtimeRequest('/automations', 'POST', body); return }
  if (typeof body.session_id !== 'string' || !body.session_id) throw failure()
  const key = storageKey(body.session_id)
  if (active.has(key)) throw failure()
  active.add(key)
  try {
    const execute = async () => {
      const previous = pending(key, body.session_id as string)
      if (previous) {
        await confirm(key, previous)
        const original = Object.fromEntries(Object.entries(previous).filter(([name]) => name !== 'creation_id'))
        if (JSON.stringify(original) !== JSON.stringify(body)) throw failure('ui.automations.creationRecovered')
        return
      }
      const submitted = {...body, creation_id: newIdentity()} as Creation
      try { localStorage.setItem(key, JSON.stringify(submitted)) }
      catch { throw failure('ui.automations.creationStorageUnavailable') }
      try {
        const saved = await runtimeRequest<Automation>('/automations', 'POST', submitted)
        if (!saved || !sameBody(saved, submitted)) throw failure()
      } catch (error) {
        if (error instanceof ApiError && error.code !== 'automation/create-exists' && !isUnconfirmedWrite(error.failure)) {
          clear(key)
          throw error
        }
        try { await confirm(key, submitted) }
        catch (check) { const unknown = failure(); unknown.cause = new AggregateError([error, check]); throw unknown }
        return
      }
      clear(key)
    }
    if (navigator.locks) {
      await navigator.locks.request(key, {ifAvailable: true}, async lock => {
        if (!lock) throw failure()
        await execute()
      })
    } else await execute()
  } finally { active.delete(key) }
}
