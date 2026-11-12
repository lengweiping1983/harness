import {t, useLocale} from '@/i18n'
import type {PermissionOperation} from '@/lib/protocol.generated'

/** 审批依据来自宿主实际判定；原始参数与自动审阅原文不用于推导原因。 */
export function PermissionOperationDetails({operation}: {operation: PermissionOperation}) {
  useLocale()
  const approval = operation.approval
  return <dl className="space-y-2 break-words text-xs leading-reading">
    <div><dt className="font-medium">{t('ui.permissions.operation')}</dt><dd>{operation.tool}</dd></div>
    <div><dt className="font-medium">{t('ui.permissions.directory')}</dt><dd className="break-all">{operation.cwd}</dd></div>
    <div><dt className="font-medium">{t('ui.permissions.impact')}</dt><dd>{t('ui.permissions.effect.' + operation.effect)}</dd></div>
    {approval ? <div><dt className="font-medium">{t('ui.permissions.approvalReason')}</dt><dd className="space-y-1 text-muted">
      <p>{t('ui.permissions.approvalPolicy.' + approval.policy)}</p>
      <ul className="list-disc space-y-1 pl-4">{approval.reasons.map(reason => <li key={reason}>{t('ui.permissions.reason.' + reason)}</li>)}</ul>
      {approval.review ? <p>{t('ui.permissions.review.' + approval.review)}</p> : null}
    </dd></div> : null}
    <div><dt className="font-medium">{t('ui.permissions.scope')}</dt><dd>{t('ui.permissions.scopeHint')}</dd></div>
  </dl>
}
