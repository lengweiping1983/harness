import type { KeyboardEvent } from 'react'

/** 页签方向键只切换当前列表中的可用按钮，不抢占输入法或带修饰键的输入。 */
export function handleTablistKeyDown(event: KeyboardEvent<HTMLElement>) {
  handleNavigationKeyDown(event, 'button[role="tab"]:not(:disabled)', false)
}

/** 页面导航保持 aria-current 语义，支持横排和竖排时的方向键。 */
export function handlePageNavigationKeyDown(event: KeyboardEvent<HTMLElement>) {
  handleNavigationKeyDown(event, 'button[data-page][aria-controls]:not(:disabled)', true)
}

function handleNavigationKeyDown(event: KeyboardEvent<HTMLElement>, selector: string, vertical: boolean) {
  if (event.defaultPrevented || event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229 || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', ...(vertical ? ['ArrowUp', 'ArrowDown'] : [])].includes(event.key)) return
  const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>(selector)).filter(tab => !tab.closest('[hidden]'))
  const index = tabs.indexOf(event.target as HTMLButtonElement)
  if (index < 0) return
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
    : (index + (event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : tabs.length - 1)) % tabs.length
  event.preventDefault()
  tabs[next]?.click()
  tabs[next]?.focus()
}
