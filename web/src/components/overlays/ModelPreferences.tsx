import {useMemo, type ReactNode} from 'react'
import {t, useLocale} from '@/i18n'
import type {ModelInfo} from '@/types'
import {noticeText} from '@/lib/noticeText'
import {cn, formatNumber} from '@/lib/utils'
import {groupModelsByProvider, modelDisplayName} from '@/lib/modelMenu'
import {modelSaveKey, useAppStore} from '@/store/app'
import {Button} from '@/components/ui/primitives'

export function DefaultModelFeedback() {
  useLocale()
  const models = useAppStore(s => s.models)
  const modelSaving = useAppStore(s => Boolean(s.modelSaves[modelSaveKey(null)]))
  const modelSave = useAppStore(s => s.defaultModelSave)
  const setDefaultModel = useAppStore(s => s.setDefaultModel)
  const dismissFeedback = useAppStore(s => s.dismissDefaultModelFeedback)
  return <>
      {modelSaving && modelSave ? <p role="status" aria-label={t("ui.components.overlays.SettingsDialog.88de44b26b76")} className="rounded-lg border border-line bg-raised p-3 text-xs text-muted [overflow-wrap:anywhere]">{t('ui.settings.models.savingDefault', {model: modelDisplayName(modelSave.target, models, t('ui.models.unavailableName'))})}</p> : null}
      {!modelSaving && modelSave?.error ? <div role="alert" aria-label={t("ui.components.overlays.SettingsDialog.fa8a07865d96")} className="space-y-2 rounded-lg border border-danger/30 bg-danger/5 p-3 text-xs text-danger [overflow-wrap:anywhere]">
        <p>{modelSave.unconfirmed ? t("ui.components.overlays.SettingsDialog.56ff57932ddb") : t("ui.components.overlays.SettingsDialog.9ce847de7d72")}</p>
        <p tabIndex={0} className="max-h-24 overflow-y-auto">{noticeText(modelSave.error)}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="xs" className="h-auto min-h-6 max-w-full whitespace-normal py-1 text-left [overflow-wrap:anywhere]" onClick={() => void setDefaultModel(modelSave.target)}>{t('ui.settings.models.retryDefault', {model: modelDisplayName(modelSave.target, models, t('ui.models.unavailableName'))})}</Button>
          <Button size="xs" variant="ghost" onClick={dismissFeedback}>{t("ui.components.overlays.SettingsDialog.a7aa73da9350")}</Button>
        </div>
      </div> : null}
  </>
}

export function ModelPreferences({models, grouped = true, heading = true, renderActions, renderFeedback, modelElements, isAvailable, modelIdentifier}: {
  models: ModelInfo[]
  grouped?: boolean
  heading?: boolean
  renderActions?: (model: ModelInfo) => ReactNode
  renderFeedback?: (model: ModelInfo) => ReactNode
  modelElements?: Map<string, HTMLDivElement>
  isAvailable?: (model: ModelInfo) => boolean
  modelIdentifier?: (model: ModelInfo) => string | undefined
}) {
  const locale = useLocale()

  const defaultModel = useAppStore((s) => s.defaultModel)
  const visibility = useAppStore((s) => s.modelVisibility)
  const visibilitySaves = useAppStore((s) => s.modelVisibilitySaves)
  const setDefaultModel = useAppStore((s) => s.setDefaultModel)
  const modelSaving = useAppStore((s) => Boolean(s.modelSaves[modelSaveKey(null)]))
  const modelSave = useAppStore((s) => s.defaultModelSave)
  const setModelVisibility = useAppStore((s) => s.setModelVisibility)
  const groups = useMemo(() => grouped ? groupModelsByProvider(models) : [{provider: null, models}], [models, locale, grouped])

  if (!models.length) return null

  return (
    <div className="space-y-4">
      {heading ? <header>
        <h3 className="ui-section-title">{t("ui.components.overlays.SettingsDialog.c98e118e0a43")}</h3>
      </header> : null}

      {groups.map((group) => (
        <section key={JSON.stringify(group.provider)} className="space-y-1.5">
          {grouped ? <h4 className="ui-group-title">{group.provider ?? t('ui.models.unspecifiedProvider')}</h4> : null}
          {group.models.map((model) => {
            const visible = visibility[model.id] !== false
            const isDefault = model.id === defaultModel
            const visibilitySave = visibilitySaves[model.id]
            const feedback = renderFeedback?.(model)
            const available = isAvailable?.(model) ?? true
            const identifier = modelIdentifier?.(model) ?? (!model.id.startsWith('harness/') ? model.id : '')
            return (
              <div
                key={model.id}
                ref={modelElements ? element => {if (element) modelElements.set(model.id, element); else modelElements.delete(model.id)} : undefined}
                className={cn("grid grid-cols-form-row items-start gap-x-3 gap-y-2 px-3 py-2", heading ? "rounded-xl border border-line" : "rounded-lg bg-raised")}
              >
                <button
                  type="button"
                  role="switch"
                  aria-checked={visible}
                  disabled={!available || visibilitySave?.pending}
                  aria-busy={visibilitySave?.pending || undefined}
                  onClick={() => void setModelVisibility(model.id, !visible)}
                  className={cn(
                    'relative flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus-visible:ring-2 focus-visible:ring-brand',
                    visible ? 'border-brand bg-brand' : 'border-line-strong bg-raised',
                    'disabled:opacity-50', visibilitySave?.pending ? 'disabled:cursor-wait' : 'disabled:cursor-not-allowed',
                  )}
                  aria-label={t(visible ? 'ui.settings.models.hideNamed' : 'ui.settings.models.showNamed', {model: model.label})}
                >
                  <span aria-hidden="true" className={cn('absolute left-0.5 h-3.5 w-3.5 rounded-full transition-transform', visible ? 'translate-x-4 bg-brand-ink' : 'bg-ink')} />
                </button>

                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-ink [overflow-wrap:anywhere]">{model.label}</p>
                  {identifier ? <p className="text-2xs text-faint [overflow-wrap:anywhere]">{identifier}</p> : null}
                  <div className="flex flex-wrap gap-x-3 text-2xs text-faint"><span>{model.context_window ? t('ui.models.contextSize', {count: formatNumber(model.context_window)}) : t('ui.models.contextUnknown')}</span>
                    {model.max_output ? <span>{t('ui.models.outputSize', {count: formatNumber(model.max_output)})}</span> : null}</div>
                  {!available ? <p className="text-2xs text-muted">{t('ui.providers.modelNotInCatalog')}</p> : null}
                  {visibilitySave?.pending ? <p role="status" aria-label={t("ui.components.overlays.SettingsDialog.dd9d81b48619")} className="mt-1 text-2xs text-muted">{t(visibilitySave.target ? 'ui.settings.models.showing' : 'ui.settings.models.hiding', {model: model.label})}</p> : null}
                  {!visibilitySave?.pending && visibilitySave?.error ? <div role="alert" aria-label={t("ui.components.overlays.SettingsDialog.cc6f6f1dda23")} className="mt-1 space-y-1 text-2xs text-danger [overflow-wrap:anywhere]">
                    <p>{visibilitySave.unconfirmed ? t("ui.components.overlays.SettingsDialog.3b189be96200") : t("ui.components.overlays.SettingsDialog.cbbbe96b2b34")}</p>
                    <p tabIndex={0} className="max-h-20 overflow-y-auto">{noticeText(visibilitySave.error)}</p>
                    <button type="button" className="text-brand underline" onClick={() => void setModelVisibility(model.id, visibilitySave.target)}>{t(visibilitySave.target ? 'ui.settings.models.retryShow' : 'ui.settings.models.retryHide', {model: model.label})}</button>
                  </div> : null}
                </div>

                <div className="col-start-2 flex flex-wrap items-center gap-1.5">
                <Button
                  size="xs"
                  className={cn("shrink-0", isDefault && "disabled:opacity-100")}
                  variant={isDefault ? 'subtle' : 'secondary'}
                  disabled={!available || isDefault || modelSaving}
                  onClick={() => void setDefaultModel(model.id)}
                >
                  {modelSaving && modelSave?.target === model.id ? t("ui.components.overlays.SettingsDialog.ff509c9ba052") : isDefault ? t("ui.components.overlays.SettingsDialog.ba336585f0a2") : t("ui.components.overlays.SettingsDialog.357211096f78")}
                </Button>
                {renderActions?.(model)}
                </div>
                {feedback ? <div className="col-span-full">{feedback}</div> : null}
              </div>
            )
          })}
        </section>
      ))}
    </div>
  )
}
