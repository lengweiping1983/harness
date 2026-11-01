import { useSyncExternalStore } from 'react'

function subscribe(notify: () => void) {
  if (typeof document === 'undefined') return () => {}
  document.addEventListener('visibilitychange', notify)
  return () => document.removeEventListener('visibilitychange', notify)
}

function snapshot() {
  return typeof document === 'undefined' || !document.hidden
}

/** 页面真正隐藏时暂停观察；失去焦点但仍可见时继续工作。 */
export function usePageVisible() {
  return useSyncExternalStore(subscribe, snapshot, () => true)
}
