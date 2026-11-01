import {t, useLocale} from '@/i18n'

export function SessionListSkeleton() {
  useLocale()
  return <div role="status" aria-label={t('ui.sidebar.loading')} className="space-y-4 px-3 py-4">
    {[0, 1].map(group => <div key={group} aria-hidden="true" className="space-y-3">
      <div className="shimmer-bar h-3 w-24 rounded" />
      {[0, 1, 2].map(row => <div key={row} className="flex h-9 items-center gap-2 rounded-lg px-2">
        <div className="shimmer-bar h-4 w-4 shrink-0 rounded" />
        <div className="shimmer-bar h-3 flex-1 rounded" />
      </div>)}
    </div>)}
  </div>
}
