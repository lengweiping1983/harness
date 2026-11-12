import { t } from '@/i18n'
/** Human-facing projection; keep original diagnostics available for inspection. */
export function modelFailure(message: unknown, code?: unknown): { title: string; hint: string } | null {
  const text = String(message ?? '')
  if (code === 'llm/provider-model-call-failed') {
    return { title: t('ui.modelFailure.providerModelCallFailed'), hint: t('ui.modelFailure.providerModelCallHint') }
  }
  if (code === 'llm/opencode-local-profile') {
    return { title: t('ui.modelFailure.localProfileRequired'), hint: t('ui.modelFailure.localProfileHint') }
  }
  if (code === 'agent/no-progress') {
    return { title: t("ui.lib.modelFailure.387b05e38bf9"), hint: t("ui.lib.modelFailure.c924e28e433e") }
  }
  if (code === 'agent/token-budget' || code === 'agent/step-limit') {
    return { title: t("ui.lib.modelFailure.d3137ebae9b0"), hint: t("ui.lib.modelFailure.2e9ca68e143c") }
  }
  const configuredLimits: Record<string, { title: string; setting: string }> = {
    'llm/thinking-limit': { title: t("ui.lib.modelFailure.284419da0f49"), setting: t("ui.lib.modelFailure.e782868fb23d") },
    'llm/stream-output-limit': { title: t("ui.lib.modelFailure.5017facaa6f9"), setting: t("ui.lib.modelFailure.6f9455895ef3") },
  }
  const limitCode = code ? String(code) : Object.keys(configuredLimits).find((item) => text.includes(`[${item}]`))
  const limit = limitCode && Object.hasOwn(configuredLimits, limitCode) ? configuredLimits[limitCode] : undefined
  if (limit) {
    return { title: limit.title, hint: t("ui.lib.modelFailure.56767a779213", {p0: (limit.setting)}) }
  }
  if (code === 'llm/empty-response' || /Provider returned an empty response/i.test(text) || text.includes('[llm/empty-response]')) {
    return { title: t("ui.lib.modelFailure.4ac0635f0432"), hint: t("ui.lib.modelFailure.82eaa0d8e5ce") }
  }
  if (code === 'llm/output-limit' || text.includes('[llm/output-limit]')) {
    return { title: t("ui.lib.modelFailure.1b503e30169c"), hint: t("ui.lib.modelFailure.c8e4e8b19e31") }
  }
  if (code === 'llm/no-answer' || text.includes('[llm/no-answer]')) {
    return { title: t("ui.lib.modelFailure.2d906f4643a2"), hint: t("ui.lib.modelFailure.7dfe30c68f48") }
  }
  if (code === 'llm/output-continuation-stalled') {
    return { title: t('ui.modelFailure.continuationStalled'), hint: t('ui.modelFailure.continuationHint') }
  }
  if (typeof code === 'string' && code.startsWith('llm/')) {
    return { title: t('ui.modelFailure.requestIncomplete'), hint: t('ui.modelFailure.requestHint') }
  }
  return null
}
