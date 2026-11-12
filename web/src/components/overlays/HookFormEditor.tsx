import { Switch } from '@/components/ui/primitives'
import { t, useLocale } from '@/i18n'
import { useEffect, useId, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Terminal, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import { hookEvents, type HookEvent } from '@/lib/projectHooks'
import { editHookForm, moveHookRule, removeHookCommand, requireHookGroup, type HookDocument, type HookForm } from '@/lib/hookForm'

const field = 'w-full min-w-0 rounded-lg border border-line bg-surface px-2.5 py-2 text-xs text-ink focus:border-brand focus:outline-none disabled:opacity-55'
export function HookFormEditor({ text, form, disabled, onChange }: { text: string; form: HookForm; disabled: boolean; onChange: (text: string) => void }) {
  useLocale()

  const [newEvent, setNewEvent] = useState<HookEvent>('PreToolUse')
  const [removing, setRemoving] = useState<string | null>(null)
  const pendingFocus = useRef<{ path: string; selector: string } | null>(null)
  const editor = useRef<HTMLFieldSetElement>(null)
  const id = useId()
  useEffect(() => {
    setRemoving(null)
    if (!pendingFocus.current) return
    const command = editor.current?.querySelector<HTMLElement>(`[data-hook-rule="${pendingFocus.current.path}"] ${pendingFocus.current.selector}`)
    pendingFocus.current = null
    command?.focus()
    command?.scrollIntoView({ block: 'nearest' })
  }, [text])
  const edit = (change: (document: HookDocument) => void) => onChange(editHookForm(text, change))
  const document = form.document
  if (!document) return null
  const room = form.commands < 32
  const errors = (path: string) => form.issues.filter(issue => issue.path === path)
  const errorId = (path: string) => `${id}-${path}`
  const errorText = (path: string) => errors(path).length ? <p id={errorId(path)} className="mt-1 text-2xs text-danger">{errors(path).map(issue => issue.message).join(' ')}</p> : null
  return <fieldset ref={editor} disabled={disabled} aria-label={t("ui.components.overlays.HookFormEditor.4d18b1e9f0ab")} className="min-w-0 space-y-3">
    <div className="space-y-2 rounded-lg bg-raised/50 p-3">
      <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.HookFormEditor.d569f7dbb9c0")}<input className={field} value={document.description ?? ''} onChange={event => edit(doc => { doc.description = event.target.value })} placeholder={t("ui.components.overlays.HookFormEditor.1f2e6e4d4964")} /></label>
      <label className="flex items-center gap-2 text-xs"><Switch checked={document.enabled !== false} onChange={event => edit(doc => { doc.enabled = event.target.checked })} className="accent-brand" />{t("ui.components.overlays.HookFormEditor.6a678608c4f3")}<span className="text-2xs text-faint">{t("ui.components.overlays.HookFormEditor.9ec35473e489")}</span></label>
    </div>
    {!form.rules.length ? <div className="rounded-xl border border-dashed border-line px-4 py-5"><h4 className="text-sm font-medium">{t("ui.components.overlays.HookFormEditor.f856732182ee")}</h4><p className="mt-1 text-xs leading-relaxed text-muted">{t("ui.components.overlays.HookFormEditor.638e7c307afe")}</p></div> : null}
    {form.rules.map(({ event, index, group }, ruleIndex) => {
      const path = `${event}:${index}`, number = ruleIndex + 1
      const groups = document.hooks[event]!
      const stage = hookEvents.find(item => item.value === event)!
      const patchGroup = (change: (group: typeof groups[number]) => void) => edit(doc => change(requireHookGroup(doc, event, index)))
      return <article key={path} data-hook-rule={path} aria-label={t("ui.components.overlays.HookFormEditor.6d7c936c0821", {p0: (number)})} className="overflow-hidden rounded-xl border border-line">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-raised/40 px-3 py-2">
          <h4 className="flex items-center gap-1.5 text-xs font-medium"><Terminal className="h-3.5 w-3.5 text-brand" />{t("ui.components.overlays.HookFormEditor.6d7c936c0821", {p0: number})}<span className="text-2xs font-normal text-faint">{stage.label}</span></h4>
          <div className="flex gap-1">
            <Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.301297887160", {p0: (number)})} disabled={index === 0} onClick={() => edit(doc => moveHookRule(doc, event, index, event, index - 1))}><ArrowUp className="h-3 w-3" /></Button>
            <Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.4220f6e35876", {p0: (number)})} disabled={index === groups.length - 1} onClick={() => edit(doc => moveHookRule(doc, event, index, event, index + 1))}><ArrowDown className="h-3 w-3" /></Button>
            <Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.372afd34d206", {p0: (number)})} onClick={() => setRemoving(removing === path ? null : path)}><Trash2 className="h-3 w-3" /></Button>
          </div>
        </div>
        {removing === path ? <div role="group" aria-label={t("ui.components.overlays.HookFormEditor.5547bfe619ec", {p0: (number)})} className="flex flex-wrap items-center gap-2 border-b border-line bg-warn/8 p-3 text-xs"><p className="flex-1">{t(group.hooks.length === 1 ? "ui.hooks.confirmDeleteOneCommand" : "ui.hooks.confirmDeleteCommands", {count: group.hooks.length})}</p><Button size="xs" variant="danger" onClick={() => edit(doc => { doc.hooks[event]!.splice(index, 1) })}>{t("ui.components.overlays.HookFormEditor.f2ad90237301")}</Button><Button size="xs" variant="ghost" onClick={() => setRemoving(null)}>{t("ui.components.overlays.HookFormEditor.a6926595ce6b")}</Button></div> : null}
        <div className="space-y-3 p-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.HookFormEditor.008a67a37f00")}<select aria-label={t("ui.components.overlays.HookFormEditor.008a67a37f00")} className={field} value={event} onChange={change => { const target = change.target.value as HookEvent; if (target === event) return; edit(doc => { const position = doc.hooks[target]?.length ?? 0; pendingFocus.current = { path: `${target}:${position}`, selector: 'select' }; moveHookRule(doc, event, index, target, position) }) }}>{hookEvents.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
            <label className="block space-y-1 text-2xs text-muted">{event === 'Stop' ? t("ui.components.overlays.HookFormEditor.cefb92a732d4") : t("ui.components.overlays.HookFormEditor.58a85f3173fa")}<input aria-label={t("ui.components.overlays.HookFormEditor.58a85f3173fa")} disabled={event === 'Stop' && (group.matcher ?? '*') === '*'} className={field} value={group.matcher ?? '*'} aria-invalid={errors(path + ':matcher').length > 0} aria-describedby={errors(path + ':matcher').length ? errorId(path + ':matcher') : undefined} onChange={change => patchGroup(group => { group.matcher = change.target.value })} placeholder={t("ui.components.overlays.HookFormEditor.53d01b3bc14a")} />{errorText(path + ':matcher')}</label>
          </div>
          <p className="text-2xs leading-relaxed text-faint">{t(stage.hint ? 'ui.hooks.eventGuidance' : 'ui.hooks.eventGuidanceWithoutHint', { hint: stage.hint, guidance: t(event === 'Stop' ? 'ui.components.overlays.HookFormEditor.ffd764865a7d' : 'ui.components.overlays.HookFormEditor.1b1ab5ac6904'), common: t('ui.components.overlays.HookFormEditor.83ee0ad5b049') })}</p>
          {errorText(path)}
          {group.hooks.map((command, commandIndex) => {
            const commandPath = `${path}:${commandIndex}`
            const patchCommand = (change: (command: typeof group.hooks[number]) => void) => patchGroup(group => change(group.hooks[commandIndex]!))
            return <fieldset key={commandIndex} aria-label={t("ui.components.overlays.HookFormEditor.3aa2230fd0ba", {p0: (number), p1: (commandIndex + 1)})} className={cn('min-w-0 space-y-2 border-l-2 pl-3', command.enabled === false || document.enabled === false ? 'border-line' : 'border-brand/35')}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-1.5 text-2xs"><Switch className="accent-brand" checked={command.enabled !== false} onChange={change => patchCommand(command => { command.enabled = change.target.checked })} />{t("ui.hooks.enableCommand", {number: commandIndex + 1})}</label>
                <div className="flex gap-1"><Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.ef2f388d6842", {p0: (commandIndex + 1)})} disabled={commandIndex === 0} onClick={() => patchGroup(group => { const [command] = group.hooks.splice(commandIndex, 1); group.hooks.splice(commandIndex - 1, 0, command!) })}><ArrowUp className="h-3 w-3" /></Button>
                  <Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.e12775ff7c1a", {p0: (commandIndex + 1)})} disabled={commandIndex === group.hooks.length - 1} onClick={() => patchGroup(group => { const [command] = group.hooks.splice(commandIndex, 1); group.hooks.splice(commandIndex + 1, 0, command!) })}><ArrowDown className="h-3 w-3" /></Button>
                  <Button size="xs" variant="ghost" aria-label={t("ui.components.overlays.HookFormEditor.abce97e4eb70", {p0: (commandIndex + 1)})} onClick={() => edit(doc => removeHookCommand(doc, event, index, commandIndex))}><Trash2 className="h-3 w-3" /></Button></div>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-hook-form">
                <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.HookFormEditor.e478978cc768")}<input aria-label={t("ui.components.overlays.HookFormEditor.e478978cc768")} className={field} value={command.statusMessage ?? t("ui.components.overlays.HookFormEditor.ce636db7bf42")} aria-invalid={errors(commandPath + ':statusMessage').length > 0} aria-describedby={errors(commandPath + ':statusMessage').length ? errorId(commandPath + ':statusMessage') : undefined} onChange={change => patchCommand(command => { command.statusMessage = change.target.value })} />{errorText(commandPath + ':statusMessage')}</label>
                <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.HookFormEditor.e814e078437b")}<input aria-label={t("ui.components.overlays.HookFormEditor.e814e078437b")} className={field} type="number" min="0.05" max="3600" step="any" value={command.timeout ?? 600} aria-invalid={errors(commandPath + ':timeout').length > 0} aria-describedby={errors(commandPath + ':timeout').length ? errorId(commandPath + ':timeout') : undefined} onChange={change => patchCommand(command => { command.timeout = change.target.value === '' ? '' : Number(change.target.value) })} />{errorText(commandPath + ':timeout')}</label>
              </div>
              <label className="block space-y-1 text-2xs text-muted">{t('ui.hooks.contextLimit')}<input aria-label={t('ui.hooks.contextLimit')} type="number" min="0" max="2147483647" step="1" className={field} value={command.additionalContextLimit ?? ''} placeholder="2500" aria-invalid={errors(commandPath + ':additionalContextLimit').length > 0} onChange={change => patchCommand(command => { if (change.target.value === '') delete command.additionalContextLimit; else command.additionalContextLimit = Number(change.target.value) })} />{errorText(commandPath + ':additionalContextLimit')}<span className="block leading-relaxed">{t('ui.hooks.contextLimitHint')}</span></label>
              <label className="block space-y-1 text-2xs text-muted">{t("ui.components.overlays.HookFormEditor.be12f84c407f")}<textarea aria-label={t("ui.components.overlays.HookFormEditor.be12f84c407f")} className={cn(field, 'min-h-20 resize-y font-mono text-xs leading-relaxed')} spellCheck={false} value={command.command} aria-invalid={errors(commandPath + ':command').length > 0} aria-describedby={errors(commandPath + ':command').length ? errorId(commandPath + ':command') : undefined} onChange={change => patchCommand(command => { command.command = change.target.value })} placeholder="python3 .harness/hooks/check.py" />{errorText(commandPath + ':command')}</label>
            </fieldset>
          })}
          <Button size="xs" variant="ghost" icon={<Plus className="h-3 w-3" />} disabled={!room} onClick={() => patchGroup(group => { group.hooks.push({ type: 'command', command: '' }) })}>{t("ui.components.overlays.HookFormEditor.9bca5988f560")}</Button>
        </div>
      </article>
    })}
    <div className="flex flex-wrap items-center gap-2"><select aria-label={t("ui.components.overlays.HookFormEditor.d9bf64c1fb46")} className={cn(field, 'w-auto max-w-full')} value={newEvent} onChange={event => setNewEvent(event.target.value as HookEvent)}>{hookEvents.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select><Button size="sm" icon={<Plus className="h-3.5 w-3.5" />} disabled={!room} onClick={() => edit(doc => {
      const groups = doc.hooks[newEvent] ?? (doc.hooks[newEvent] = [])
      pendingFocus.current = { path: `${newEvent}:${groups.length}`, selector: 'textarea' }
      groups.push({ matcher: '*', hooks: [{ type: 'command', command: '' }] })
    })}>{t("ui.components.overlays.HookFormEditor.c9f9e18b0623")}</Button><span className="text-2xs tabular-nums text-faint">{t("ui.hooks.commandCount", {count: form.commands, limit: 32})}</span></div>
    {form.issues.filter(issue => issue.path === 'hooks' || hookEvents.some(event => event.value === issue.path)).map(issue => <p key={issue.path} role="alert" className="text-2xs text-danger">{issue.message}</p>)}
    <details className="rounded-lg bg-raised/50 p-3 text-2xs text-muted"><summary className="cursor-pointer font-medium text-ink">{t("ui.components.overlays.HookFormEditor.04b6893da594")}</summary><div className="mt-2 space-y-2 leading-relaxed"><p>{t("ui.components.overlays.HookFormEditor.e07fe19e27ea")}</p><p>{t("ui.components.overlays.HookFormEditor.6fa55cedf807")}</p><pre className="overflow-auto whitespace-pre-wrap break-all font-mono">{t("ui.components.overlays.HookFormEditor.cb7dd6b1e20d")}</pre><p>{t("ui.components.overlays.HookFormEditor.13e52579a1cd")}</p></div></details>
  </fieldset>
}
