import type { Block, TeamBlock } from '@/types'

export type MemberPreviewFrame = NonNullable<TeamBlock['previews']>[string]

/** 从最近的团队记录向前查找，保留原始引用，让无关消息不触发详情重绘。 */
export function latestMemberPreview(blocks: readonly Block[] | undefined, agentId: string): MemberPreviewFrame | null {
  if (!blocks) return null
  for (let index = blocks.length - 1; index >= 0; index--) {
    const block = blocks[index]!
    if (block.kind !== 'team' || !block.previews) continue
    for (const key in block.previews) {
      const frame = block.previews[key]!
      if (frame.agentId === agentId) return frame
    }
  }
  return null
}
