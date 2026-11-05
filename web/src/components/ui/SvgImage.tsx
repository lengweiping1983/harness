import {useId, useMemo} from 'react'

/** 样式与 SMIL 只在图片上下文中运行，不能将源码改为内联 SVG 或 object。 */
export function SvgImage({markup, alt, replay = 0}: {markup: string; alt: string; replay?: number}) {
  const instance = useId()
  const src = useMemo(() => `data:image/svg+xml;charset=utf-8,${encodeURIComponent(
    `${markup}<!-- preview:${instance};replay:${replay} -->`,
  )}`, [markup, instance, replay])
  return <img src={src} alt={alt} className="mx-auto block h-auto w-full max-w-full" />
}
