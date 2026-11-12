import {isRecord} from './guards'
import type { ResourceText } from '@/types'
import { ResourceError, failureText } from './resourceError'
import { t } from '@/i18n'
import { hookEvents, type HookEvent } from './projectHooks'

export type HookCommand = { type: 'command'; command: string; timeout?: number | string; statusMessage?: string; enabled?: boolean; additionalContextLimit?: number }
export type HookGroup = { matcher?: string; hooks: HookCommand[] }
export type HookDocument = { description?: string; enabled?: boolean; hooks: Partial<Record<HookEvent, HookGroup[]>> }
export type HookIssue = { path: string; message: string }
export type HookRule = { event: HookEvent; index: number; group: HookGroup }
export type HookForm = { document: HookDocument | null; rules: HookRule[]; issues: HookIssue[]; error: ResourceText; commands: number }
const keys = (value: Record<string, unknown>, supported: string[]) => Object.keys(value).every(key => supported.includes(key))
const events = hookEvents.map(event => event.value)
const length = (value: string) => Array.from(value).length

/** Read without rewriting: unsupported fields stay in JSON, never disappear in a form save. */
export function readHookForm(text: string): HookForm {
  const rules: HookRule[] = [], issues: HookIssue[] = []
  let commands = 0
  try {
    const value: unknown = JSON.parse(text)
    if (!isRecord(value) || !keys(value, ['description', 'enabled', 'hooks']) ||
        value.description !== undefined && typeof value.description !== 'string' ||
        value.enabled !== undefined && typeof value.enabled !== 'boolean' || !isRecord(value.hooks) || !keys(value.hooks, events)) throw new ResourceError({key: "ui.lib.hookForm.870f3566c2ba"})
    // Keep source event/group/command order. The executor runs matching commands in this order.
    for (const [event, groups] of Object.entries(value.hooks)) {
      if (!Array.isArray(groups)) throw new ResourceError({key: "ui.lib.hookForm.a91a5f2f055a"})
      if (groups.length > 32) issues.push({ path: event, message: t("ui.lib.hookForm.5746d384dbef") })
      groups.forEach((group, index) => {
        const path = `${event}:${index}`
        if (!isRecord(group) || !keys(group, ['matcher', 'hooks']) || group.matcher !== undefined && typeof group.matcher !== 'string' || !Array.isArray(group.hooks)) throw new ResourceError({key: "ui.lib.hookForm.7b444910fad1"})
        const matcher = group.matcher ?? '*'
        if (length(matcher) > 256) issues.push({ path: path + ':matcher', message: t("ui.lib.hookForm.900e4fe1baaf") })
        if (!group.hooks.length || group.hooks.length > 32) issues.push({ path, message: t("ui.lib.hookForm.0d7e80ff497d") })
        group.hooks.forEach((handler, commandIndex) => {
          if (!isRecord(handler) || !keys(handler, ['type', 'command', 'timeout', 'statusMessage', 'enabled', 'additionalContextLimit']) || handler.type !== 'command' || typeof handler.command !== 'string' ||
              handler.statusMessage !== undefined && typeof handler.statusMessage !== 'string' ||
              handler.enabled !== undefined && typeof handler.enabled !== 'boolean' ||
              handler.timeout !== undefined && typeof handler.timeout !== 'number' && typeof handler.timeout !== 'string') throw new ResourceError({key: "ui.lib.hookForm.d962650a34b0"})
          const commandPath = `${path}:${commandIndex}`
          if (!handler.command.trim() || length(handler.command) > 4096 || handler.command.includes('\0')) issues.push({ path: commandPath + ':command', message: t("ui.lib.hookForm.67370017590c") })
          const label = handler.statusMessage ?? t("ui.lib.hookForm.ce636db7bf42")
          if (!label.trim() || length(label) > 160) issues.push({ path: commandPath + ':statusMessage', message: t("ui.lib.hookForm.7b28e9f45bcf") })
          const timeout = handler.timeout ?? 600
          if (typeof timeout === 'number' && !Number.isFinite(timeout)) throw new ResourceError({key: "ui.lib.hookForm.e0584bcced01"})
          if (typeof timeout !== 'number' || timeout < .05 || timeout > 3600) issues.push({ path: commandPath + ':timeout', message: t("ui.lib.hookForm.5841a0358035") })
          if (handler.additionalContextLimit !== undefined && (typeof handler.additionalContextLimit !== 'number' || !Number.isInteger(handler.additionalContextLimit) || handler.additionalContextLimit < 0 || handler.additionalContextLimit > 2147483647)) issues.push({ path: commandPath + ':additionalContextLimit', message: t('ui.hooks.invalidContextLimit') })
          commands++
        })
        rules.push({ event: event as HookEvent, index, group: group as HookGroup })
      })
    }
    if (commands > 32) issues.push({ path: 'hooks', message: t("ui.lib.hookForm.ae5b1454eb6b") })
    return { document: value as HookDocument, rules, issues, error: '', commands }
  } catch (failure) {
    return { document: null, rules: [], issues: [], commands: 0, error: failure instanceof SyntaxError ? {key: "ui.lib.hookForm.5d6f65ba77be"} : failureText(failure) }
  }
}

export function editHookForm(text: string, edit: (document: HookDocument) => void): string {
  const form = readHookForm(text)
  if (!form.document) throw typeof form.error === 'string' ? new Error(form.error) : new ResourceError(form.error)
  edit(form.document)
  return JSON.stringify(form.document, null, 2)
}

export function requireHookGroup(document: HookDocument, event: HookEvent, index: number): HookGroup {
  const group = document.hooks[event]?.[index]
  if (!group) throw new ResourceError({key: "ui.lib.hookForm.3b18cb93efff"})
  return group
}

export function moveHookRule(document: HookDocument, event: HookEvent, index: number, target: HookEvent, position: number): void {
  const group = requireHookGroup(document, event, index)
  document.hooks[event]!.splice(index, 1)
  const groups = document.hooks[target] ?? (document.hooks[target] = [])
  groups.splice(Math.max(0, Math.min(position, groups.length)), 0, group)
}

export function removeHookCommand(document: HookDocument, event: HookEvent, index: number, command: number): void {
  const group = requireHookGroup(document, event, index)
  if (!group.hooks[command]) throw new ResourceError({key: "ui.lib.hookForm.97f0222caa24"})
  group.hooks.splice(command, 1)
  if (!group.hooks.length) document.hooks[event]!.splice(index, 1)
}
