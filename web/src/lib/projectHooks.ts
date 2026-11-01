import { ResourceError } from './resourceError'
import { t } from '@/i18n'
import type { HookActivity } from './protocol.generated'
export type HookEvent = HookActivity['event']
export type HookRow = { id: string; event: HookEvent; matcher: string; command: string; timeout: number; label: string; enabled: boolean }
export type HookRun = { id: string; session_id: string; event: HookEvent; tool: string; label: string; command: string; status: string; reason: string; stdout: string; stderr: string; stdout_dropped?: number; stderr_dropped?: number; started: number; duration_ms: number }
export type HookSnapshot = { root: string; source: string; fingerprint: string; trusted: boolean; document: unknown; text?: string; rows: HookRow[]; error: string; runs: HookRun[] }
export const hookEvents: { value: HookEvent; label: string; hint: string }[] = [
  { value: 'PreToolUse', get label() { return t('ui.lib.projectHooks.aa173c3d9fab') }, get hint() { return t('ui.lib.projectHooks.fa004217020d') } },
  { value: 'PostToolUse', get label() { return t('ui.lib.projectHooks.9cc442a87542') }, get hint() { return t('ui.lib.projectHooks.a50852228a2a') } },
  { value: 'PostToolUseFailure', get label() { return t('ui.lib.projectHooks.ef5402b849d6') }, get hint() { return t('ui.lib.projectHooks.b7ae96f0a83c') } },
  { value: 'Stop', get label() { return t('ui.lib.projectHooks.2e2a6cf056dd') }, get hint() { return t('ui.lib.projectHooks.21344aa8e4d9') } },
  ...(['PermissionRequest', 'PreCompact', 'PostCompact', 'SessionStart', 'SessionEnd', 'UserPromptSubmit', 'SubagentStart', 'SubagentStop', 'Interrupt'] as HookEvent[]).map(value => ({ value, label: value, hint: '' })),
]
export const hookStatuses: Record<string, string> = Object.setPrototypeOf({ get running() { return t('ui.lib.projectHooks.1f0eb99b7ed0') }, get passed() { return t('ui.lib.projectHooks.1e9f2561b7cf') }, get blocked() { return t('ui.lib.projectHooks.61781abf9905') }, get failed() { return t('ui.member.status.failed') }, get timed_out() { return t('ui.lib.projectHooks.e512cf016f96') }, get cancelled() { return t('ui.trace.status.cancelled') }, get skipped() { return t('ui.lib.projectHooks.f95ea7f4c063') }, get interrupted() { return t('ui.lib.projectHooks.e6bedaccc265') }, get cleanup_failed() { return t('ui.lib.projectHooks.68585547b4db') }, get stopped() { return t('ui.trace.status.stopped') } }, null)
export const hookTemplates = [
  { get label() { return t('ui.lib.projectHooks.ea7ec7f83ef2') }, event: 'PreToolUse' as HookEvent, matcher: 'bash', handler: {
    type: 'command', get statusMessage() { return t('ui.lib.projectHooks.60577a07e698') }, timeout: 10,
    get command() { return t('ui.lib.projectHooks.9f030a8d2a78') },
  } },
  { get label() { return t('ui.lib.projectHooks.847839243070') }, event: 'PostToolUse' as HookEvent, matcher: 'write|edit', handler: {
    type: 'command', get statusMessage() { return t('ui.lib.projectHooks.eb0d9f02fe39') }, timeout: 120, command: 'python3 -m unittest discover -s tests -t .',
  } },
  { get label() { return t('ui.lib.projectHooks.e3c3cf6deb13') }, event: 'PostToolUseFailure' as HookEvent, matcher: '*', handler: {
    type: 'command', get statusMessage() { return t('ui.lib.projectHooks.ecac24a01b97') }, timeout: 10,
    get command() { return t('ui.lib.projectHooks.94c9b1337f0c') },
  } },
  { get label() { return t('ui.lib.projectHooks.b90bb7ef2bce') }, event: 'Stop' as HookEvent, matcher: '*', handler: {
    type: 'command', get statusMessage() { return t('ui.lib.projectHooks.39c05e20bab9') }, timeout: 120,
    command: `python3 -m unittest discover -s tests -t . 1>&2 || exit 2`,
  } },
]

export function addHookTemplate(text: string, index: number): string {
  const template = hookTemplates[index]
  let document
  try { document = JSON.parse(text) } catch (error) {
    if (error instanceof SyntaxError) throw new ResourceError({key: 'ui.lib.hookForm.5d6f65ba77be'})
    throw error
  }
  if (!document || typeof document !== 'object' || Array.isArray(document) || !document.hooks || typeof document.hooks !== 'object' || Array.isArray(document.hooks)) throw new ResourceError({key: "ui.lib.projectHooks.b11f90cf5cc8"})
  const groups = document.hooks[template.event] ?? []
  if (!Array.isArray(groups)) throw new ResourceError({key: "ui.lib.projectHooks.275e2edd9b13"})
  document.hooks[template.event] = [...groups, { matcher: template.matcher, hooks: [template.handler] }]
  return JSON.stringify(document, null, 2)
}

export function hookPath(root: string, suffix = ''): string {
  return '/hooks' + suffix + (root === '@user' ? '?scope=user' : root ? '?root=' + encodeURIComponent(root) : '')
}

// Per-window drafts retain their original disk version so refresh cannot silently
// turn a stale edit into authority to overwrite someone else's configuration.
import { createWindowDraftStorage } from './windowDraftStorage'
const draftStorage = createWindowDraftStorage()
export type HookDraft = { text: string; base: string; savedText: string }
export function readHookDraft(root: string): HookDraft | null {
  try {
    const raw = draftStorage.read('harness:hook-draft:' + root)
    if (!raw) return null
    const data = JSON.parse(raw)
    return data && typeof data.text === 'string' && typeof data.base === 'string' && typeof data.savedText === 'string' ? data : null
  } catch { return null }
}
export function writeHookDraft(root: string, draft: HookDraft | null): boolean {
  return draftStorage.write('harness:hook-draft:' + root, draft ? JSON.stringify(draft) : null)
}
