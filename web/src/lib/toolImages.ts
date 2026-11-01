import { t } from '@/i18n'
import type { ToolBlockState } from '@/types'

export interface ViewedImage { src: string; alt: string }
const DATA_IMAGE = /^data:image\/(?:png|jpeg|jpg|gif|webp|bmp);base64,[A-Za-z0-9+/]+={0,2}$/gm
const MAX_IMAGE_URI = 7 * 1024 * 1024

/** 使用已提交结果中的字节快照；不重新读取可能已经被修改或删除的原文件。 */
export function toolImages(block: ToolBlockState): ViewedImage[] {
  if (!['read_image', 'read_images', 'view_image'].includes(block.name) || typeof block.output !== 'string' || block.output.length > 30 * 1024 * 1024) return []
  const display = block.display as { path?: string; images?: { path?: string }[] } | undefined
  const input = block.input as { path?: string; paths?: string[] } | undefined
  const images: ViewedImage[] = []
  for (const match of block.output.matchAll(DATA_IMAGE)) {
    if (match[0].length > MAX_IMAGE_URI || images.length === 8) continue
    const index = images.length
    const path = display?.images?.[index]?.path ?? display?.path ?? input?.paths?.[index] ?? input?.path
    images.push({ src: match[0], alt: typeof path === 'string' ? path : t("ui.lib.toolImages.7f63df151f51", {p0: (index + 1)}) })
  }
  return images
}

export function imageOutputSummary(output: string): string {
  return output.replace(DATA_IMAGE, t("ui.lib.toolImages.c4d574abcb3a")).trim()
}
