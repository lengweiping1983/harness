import {useLayoutEffect, useState, type CSSProperties, type RefObject} from 'react'
import {anchoredMenuBounds} from '@/lib/anchoredMenu'

// 通用菜单默认像素宽度；模型菜单单独测量现有样式令牌。
const MENU_WIDTH = 336

// 根据可见空间把浮层菜单定位到锚点上方或下方。原先菜单是 composer 里的 absolute 元素，而
// .composer-card 带 overflow-hidden（圆角裁切必需），菜单上半部分会被输入卡
// 裁掉 —— 看得见却点不到。fixed + portal 脱离这棵子树才躲得开。
export function useAnchoredMenu(anchor: RefObject<HTMLElement | null>, open: boolean, preferredWidth: number | 'anchor' | 'model' = MENU_WIDTH) {
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' })

  useLayoutEffect(() => {
    if (!open) {
      setStyle({ visibility: 'hidden' })
      return
    }
    const el = anchor.current
    if (!el) return

    const measure = preferredWidth === 'model' ? document.createElement('div') : null
    if (measure) {
      measure.className = 'w-model-menu'
      measure.setAttribute('aria-hidden', 'true')
      Object.assign(measure.style, {position: 'fixed', visibility: 'hidden', pointerEvents: 'none', height: '0'})
      document.body.append(measure)
    }
    const place = () => {
      const rect = el.getBoundingClientRect()
      const viewport = window.visualViewport
      const bounds = anchoredMenuBounds(rect, { left: viewport?.offsetLeft ?? 0, top: viewport?.offsetTop ?? 0,
        width: viewport?.width ?? window.innerWidth, height: viewport?.height ?? window.innerHeight }, preferredWidth === 'anchor' ? rect.width : measure ? measure.getBoundingClientRect().width : preferredWidth as number)
      setStyle({
        position: 'fixed',
        left: bounds.left,
        top: bounds.top,
        width: bounds.width,
        maxHeight: bounds.maxHeight,
        overflowY: 'auto',
        overscrollBehavior: 'contain',
        transform: bounds.placement === 'above' ? 'translateY(-100%)' : undefined,
        visibility: 'visible',
      })
    }

    place()
    const observer = new ResizeObserver(place)
    observer.observe(el)
    if (measure) observer.observe(measure)
    window.addEventListener('resize', place)
    window.addEventListener('scroll', place, true)
    window.visualViewport?.addEventListener('resize', place)
    window.visualViewport?.addEventListener('scroll', place)
    return () => {
      observer.disconnect()
      measure?.remove()
      window.removeEventListener('resize', place)
      window.removeEventListener('scroll', place, true)
      window.visualViewport?.removeEventListener('resize', place)
      window.visualViewport?.removeEventListener('scroll', place)
    }
  }, [anchor, open, preferredWidth])

  return style
}
