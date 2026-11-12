import { t, getLocale } from '@/i18n'
export type Schedule = { kind: 'once'; at: number } | { kind: 'interval'; seconds: number } | { kind: 'daily'; time: string } | { kind: 'weekly'; time: string; days: number[] } | { kind: 'monthly'; time: string; monthdays: number[] } | { kind: 'yearly'; time: string; months: number[]; monthdays: number[] }
export type Automation = { id: string; name: string; prompt: string; session_id: string; mode: 'session' | 'fresh'; schedule: Schedule; timezone: string; due: number; enabled: number; running: number; failures: number; last_error: string | null }
export type AutomationRun = { id: string; automation_id: string; name: string | null; session_id: string | null; started: number; finished: number | null; status: string; trigger: string; unread: number; result: string | null }
export type AutomationPayload = { automations: Automation[]; runs: AutomationRun[]; scheduler: { running: boolean; owner: boolean; last_error: string | null; max_concurrent: number } }
export function automationState(task: Automation, runs: AutomationRun[]): 'running' | 'active' | 'paused' | 'completed' {
  if (task.running) return 'running'
  if (task.enabled) return 'active'
  const latest = runs.find(run => run.automation_id === task.id)
  return task.schedule.kind === 'once' && latest?.status === 'completed' && latest.finished !== null && latest.finished >= task.due ? 'completed' : 'paused'
}
const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
export function weekdayLabels(): string[] { return WEEKDAY_KEYS.map(day => t('ui.automations.weekday.' + day)) }
export const ZONES = ['Asia/Shanghai', 'Asia/Tokyo', 'UTC']
export function dateLabel(timestamp: number, zone = 'Asia/Shanghai') {
  return new Intl.DateTimeFormat(getLocale(), { timeZone: zone, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(timestamp * 1000))
}
export function scheduleLabel(schedule: Schedule) {
  if (schedule.kind === 'once') return t("ui.lib.automations.7c04effc4339")
  if (schedule.kind === 'interval') {
    const hours = schedule.seconds % 3600 === 0
    const count = schedule.seconds / (hours ? 3600 : 60)
    return t(hours ? (count === 1 ? 'ui.automations.everyOneHour' : 'ui.automations.everyHours') : (count === 1 ? 'ui.automations.everyOneMinute' : 'ui.automations.everyMinutes'), {count})
  }
  if (schedule.kind === 'daily') return t("ui.lib.automations.1cd663150c88", {p0: (schedule.time)})
  if (schedule.kind === 'monthly') return t("ui.lib.automations.monthlyLabel", {p0: (schedule.monthdays.join(t('ui.common.list_separator'))), p1: (schedule.time)})
  if (schedule.kind === 'yearly') return t("ui.lib.automations.yearlyLabel", {p0: (schedule.months.join(t('ui.common.list_separator'))), p1: (schedule.monthdays.join(t('ui.common.list_separator'))), p2: (schedule.time)})
  const days = weekdayLabels()
  return t("ui.lib.automations.d47891fbea69", {p0: (schedule.days.map((day) => days[day]).join(t('ui.common.list_separator'))), p1: (schedule.time)})
}
export function localInput(timestamp: number, zone: string) {
  const parts = new Intl.DateTimeFormat('sv-SE', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(timestamp * 1000))
  const get = (type: string) => parts.find((part) => part.type === type)?.value
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`
}
export function inputTimestamp(value: string, zone: string) {
  const offsets: Record<string, string> = { 'Asia/Shanghai': '+08:00', 'Asia/Tokyo': '+09:00', UTC: 'Z' }
  return new Date(value + ':00' + offsets[zone]).getTime() / 1000
}
export function runResult(run: AutomationRun): { text?: string; error?: string } {
  try {
    const data = JSON.parse(run.result ?? '{}')
    if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('Invalid result')
    return { text: typeof data.text === 'string' ? data.text : undefined, error: typeof data.error === 'string' ? data.error : undefined }
  } catch { return { error: t("ui.lib.automations.d46beb7ef70f") } }
}
