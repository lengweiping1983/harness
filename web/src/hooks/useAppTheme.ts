import {useEffect} from 'react'
import {useAppStore} from '@/store/app'
import {applyTheme} from '@/lib/theme'

/** 所有页面共用系统主题跟随与用户设置，不需要读取宿主配置。 */
export function useAppTheme() {
  const theme = useAppStore(state => state.theme)
  useEffect(() => {applyTheme(theme)}, [theme])
  useEffect(() => {
    if (theme !== 'system') return
    const query = window.matchMedia?.('(prefers-color-scheme: dark)')
    if (!query) return
    const changed = () => useAppStore.getState().setTheme('system')
    query.addEventListener('change', changed)
    return () => query.removeEventListener('change', changed)
  }, [theme])
}
