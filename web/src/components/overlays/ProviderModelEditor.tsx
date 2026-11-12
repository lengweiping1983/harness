import {useRef, useState, type RefObject} from 'react'
import {Plus, Trash2} from 'lucide-react'
import {t, useLocale} from '@/i18n'
import {newProviderModel, validProviderModelId, type ProviderModel} from '@/lib/providerConnections'
import {Button} from '@/components/ui/primitives'

const field = 'w-full min-w-0 rounded-lg border border-line bg-canvas px-3 py-2 text-compact'

/** 手动添加与已有模型编辑共用字段和参数语义。 */
export function ProviderModelFields({value, disabled, onChange, manual = false, modelInput}: {
  value: ProviderModel
  disabled: boolean
  onChange: (value: ProviderModel) => void
  manual?: boolean
  modelInput?: RefObject<HTMLInputElement>
}) {
  useLocale()
  return <div className="space-y-3">
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      <label className="block min-w-0 space-y-1 text-xs">
        <span>{t('ui.providers.model')}</span>
        <input ref={modelInput} className={field} value={value.model} disabled={disabled} autoComplete="off" spellCheck={false} autoCapitalize="none" data-lpignore="true" data-1p-ignore="true" name={`provider-model-${value.id}`} aria-label={manual ? t('ui.providers.manualModel') : undefined} placeholder={manual ? t('ui.providers.manualModel') : undefined} aria-invalid={!!value.model.trim() && !validProviderModelId(value.model)} onChange={event => onChange({...value, model: event.target.value})} />
      </label>
      <label className="block min-w-0 space-y-1 text-xs">
        <span>{t('ui.providers.modelName')}</span>
        <input className={field} value={value.name} disabled={disabled} autoComplete="off" placeholder={manual ? t('ui.providers.modelNameOptional') : undefined} onChange={event => onChange({...value, name: event.target.value})} />
      </label>
      {(['context_window', 'max_output_tokens'] as const).map(name => <label key={name} className="block min-w-0 space-y-1 text-xs">
        <span>{t(`ui.providers.${name}`)}</span>
        <input className={field} type="number" min="1" step="1" value={value[name] ?? ''} placeholder={t('ui.providers.metadataUnknown')} disabled={disabled} onChange={event => onChange({...value, [name]: event.target.value ? Number(event.target.value) : undefined})} />
      </label>)}
    </div>
    <fieldset className="min-w-0 space-y-2">
      <legend className="text-xs text-muted">{t('ui.providers.inputTypes')}</legend>
      <div className="flex flex-wrap gap-2">{['text', 'image', 'video'].map(modality => <label key={modality} className="inline-flex items-center gap-2 rounded-lg border border-line px-3 py-2 text-xs">
        <input type="checkbox" checked={value.input.includes(modality)} disabled={disabled || value.input.length === 1 && value.input.includes(modality)} onChange={event => onChange({...value, input: event.target.checked ? [...value.input, modality] : value.input.filter(item => item !== modality)})} />
        {t(`ui.providers.${modality}`)}
      </label>)}</div>
    </fieldset>
    <ReasoningEditor value={value.reasoning_efforts} disabled={disabled} onChange={reasoning_efforts => onChange({...value, reasoning_efforts})} />
    <label className="block space-y-1 text-xs">
      <span>{t('ui.providers.reasoningFormat')}</span>
      <select className={field} aria-label={t('ui.providers.reasoningFormat')} disabled={disabled} value={typeof value.compat?.thinkingFormat === 'string' ? value.compat.thinkingFormat : ''} onChange={event => {
        const compat = {...value.compat}
        if (event.target.value) compat.thinkingFormat = event.target.value
        else delete compat.thinkingFormat
        onChange({...value, compat})
      }}>
        <option value="">{t('ui.providers.inherit')}</option>
        <option value="openai">{t('ui.providers.reasoningFormatFlat')}</option>
        <option value="openrouter">{t('ui.providers.reasoningFormatNested')}</option>
        {typeof value.compat?.thinkingFormat === 'string' && !['openai', 'openrouter'].includes(value.compat.thinkingFormat) ? <option value={value.compat.thinkingFormat}>{t('ui.providers.reasoningFormatOther', {format: value.compat.thinkingFormat})}</option> : null}
      </select>
      <span className="block text-muted">{t('ui.providers.reasoningFormatHint')}</span>
    </label>
  </div>
}

/** 添加前完成参数填写；成功后重置草稿并留在输入处连续添加。 */
export function ManualModelEditor({models, disabled, onAdd}: {
  models: ProviderModel[]
  disabled: boolean
  onAdd: (model: ProviderModel) => boolean
}) {
  useLocale()
  const [draft, setDraft] = useState(() => newProviderModel(''))
  const [addedName, setAddedName] = useState('')
  const modelInput = useRef<HTMLInputElement>(null)
  const editor = useRef<HTMLFieldSetElement>(null)
  const composing = useRef(false)
  const duplicate = models.some(model => model.model === draft.model.trim())
  const invalidId = !!draft.model.trim() && !validProviderModelId(draft.model)
  const invalidCapacity = [draft.context_window, draft.max_output_tokens].some(value => value !== undefined && (!Number.isSafeInteger(value) || value < 1))
  const ready = !disabled && validProviderModelId(draft.model) && !duplicate && !invalidCapacity
  const add = () => {
    if (!ready || composing.current) return
    const inputs = editor.current?.querySelectorAll<HTMLInputElement | HTMLSelectElement>('input, select')
    if (inputs && !Array.from(inputs).every(input => input.reportValidity())) return
    const model = {...draft, model: draft.model.trim(), name: draft.name.trim() || draft.model.trim()}
    if (!onAdd(model)) return
    setAddedName(model.name)
    setDraft(newProviderModel(''))
    modelInput.current?.focus()
  }
  return <fieldset ref={editor} disabled={disabled} className="min-w-0 space-y-4 rounded-xl border border-line bg-surface p-3 sm:p-4"
    onCompositionStart={() => {composing.current = true}} onCompositionEnd={() => {composing.current = false}}
    onKeyDown={event => {
      if (event.key !== 'Enter' || !(event.target instanceof HTMLInputElement) || event.target.type === 'checkbox') return
      event.preventDefault()
      event.stopPropagation()
      if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return
      add()
    }}>
    <legend className="px-1 text-xs font-medium text-ink">{t('ui.providers.manualConfiguration')}</legend>
    <ProviderModelFields value={draft} disabled={disabled} manual modelInput={modelInput} onChange={value => {setDraft(value); setAddedName('')}} />
    {invalidId || duplicate || invalidCapacity ? <p role="alert" className="text-xs text-danger">{t(invalidId ? 'ui.providers.invalidModelId' : duplicate ? 'ui.providers.duplicateModel' : 'ui.providers.invalidModelCapacity')}</p> : null}
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
      <p role={addedName ? 'status' : undefined} className="min-w-0 flex-1 text-xs text-muted [overflow-wrap:anywhere]">{addedName ? t('ui.providers.modelAdded', {model: addedName}) : t('ui.providers.manualConfigurationHint')}</p>
      <Button size="sm" variant="primary" disabled={!ready} onClick={add} icon={<Plus aria-hidden className="h-4 w-4" />}>{t('ui.providers.addModel')}</Button>
    </div>
  </fieldset>
}

function ReasoningEditor({value, disabled, onChange}: {value: ProviderModel['reasoning_efforts']; disabled: boolean; onChange: (value: ProviderModel['reasoning_efforts']) => void}) {
  const enabled = value !== false && value !== undefined
  return <div className="space-y-2">
    <label className="block space-y-1 text-xs"><span>{t('ui.providers.reasoningMode')}</span><select className={field} aria-label={t('ui.providers.reasoningMode')} disabled={disabled} value={value === undefined ? 'inherit' : value === false ? 'disabled' : 'custom'} onChange={event => onChange(event.target.value === 'inherit' ? undefined : event.target.value === 'disabled' ? false : {off: null, low: 'low', medium: 'medium', high: 'high'})}>
      <option value="inherit">{t('ui.providers.inherit')}</option><option value="disabled">{t('ui.providers.disableReasoning')}</option><option value="custom">{t('ui.providers.customReasoning')}</option>
    </select></label>
    {enabled ? <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{Object.entries(value).map(([level, parameter]) => <label key={level} className="flex min-w-0 items-center gap-2 text-xs">
      <span className="w-16 shrink-0">{level}</span><input className={field} disabled={disabled} aria-label={t('ui.providers.reasoningParameter', {level})} value={parameter ?? ''} onChange={event => onChange({...value, [level]: event.target.value || null})} />
      <Button size="xs" variant="ghost" disabled={disabled || Object.keys(value).length === 1} aria-label={t('ui.providers.removeReasoning', {level})} onClick={() => {const next = {...value}; delete next[level]; onChange(next)}}><Trash2 aria-hidden className="h-4 w-4" /></Button>
    </label>)}</div> : null}
    {enabled ? <select className={field} disabled={disabled} value="" aria-label={t('ui.providers.addReasoning')} onChange={event => onChange({...value, [event.target.value]: event.target.value})}>
      <option value="">{t('ui.providers.addReasoning')}</option>{['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'].filter(level => !(level in value)).map(level => <option key={level} value={level}>{level}</option>)}
    </select> : null}
  </div>
}
