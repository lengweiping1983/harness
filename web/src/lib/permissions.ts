import { t } from '@/i18n'
export const permissionProfiles = [
  { id: 'request_approval', get label() { return t("ui.lib.permissions.205050642550") }, get description() { return t("ui.lib.permissions.860aef6862bd") }, icon: 'hand' },
  { id: 'auto_review', get label() { return t("ui.lib.permissions.79f1c34bd0bc") }, get description() { return t("ui.lib.permissions.f1458429fcb1") }, icon: 'shield' },
  { id: 'full_access', get label() { return t("ui.lib.permissions.e7adfca83278") }, get description() { return t("ui.lib.permissions.81a2e13d6e04") }, icon: 'unlock' },
] as const

export type PermissionProfile = typeof permissionProfiles[number]['id']
export function permissionProfile(value: string): PermissionProfile | 'read_only' | null {
  if (value === 'read_only') return value
  return permissionProfiles.some((profile) => profile.id === value) ? value as PermissionProfile : null
}

export function permissionDecision(decisions: unknown[], reviewer?: unknown, reason?: unknown): string {
  const labels: Record<string, string> = { allow: t("ui.lib.permissions.25726f4d3826"), deny: t("ui.lib.permissions.89f9c6e74e90"), ask: t("ui.lib.permissions.b0a754c0e843") }
  const decision = decisions.map((value) => Object.hasOwn(labels, String(value)) ? labels[String(value)] : String(value)).join(t('ui.common.list_separator')) || t("ui.lib.permissions.94bc3d40defe")
  const reviewed = reviewer === 'auto_review' ? t('ui.permissions.automatic_decision', { decision }) : decision
  return typeof reason === 'string' && reason ? t('ui.permissions.with_reason', { decision: reviewed, reason }) : reviewed
}
