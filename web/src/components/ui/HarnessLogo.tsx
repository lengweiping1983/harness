/** 品牌名保持原文；所有界面引用同一份矢量资源。 */
export function HarnessLogo({className}: {className?: string}) {
  return <img src="/brand/harness.svg" alt="Harness" width={128} height={128} className={className} draggable={false} />
}
