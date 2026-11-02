import {isTableKind,type TableKind} from './tableData'
import {isChartKind, type ChartKind} from './chartSpec'
import type { ToolBlockState } from '@/types'

/**
 * show_widget 工具的界面载荷解析。
 *
 * 后端把完整小组件放在 ``display.widget``（模型上下文只保留确认文本），
 * 前端只认这一处来源；历史事件损坏或来自其他工具时返回 null，
 * 由通用工具结果视图兜底，不猜、不补。
 */

export type WidgetKind = 'svg' | 'html' | ChartKind | TableKind

export interface WidgetPayload {
  title: string
  code: string
  kind: WidgetKind
  chars: number
  loadingMessages: string[]
}

/** 工具运行中可用的预览信息（标题与类型提示来自已解析的调用参数）。 */
export interface WidgetPreview {
  title: string
  loadingMessages: string[]
}

const record = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === 'object' && !Array.isArray(value)

const nonBlank = (value: unknown): value is string => typeof value === 'string' && Boolean(value.trim())

function loadingMessages(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => nonBlank(item)).map((item) => item.trim()).slice(0, 4)
}

export function parseWidgetPayload(value: unknown): WidgetPayload | null {
  if (!record(value) || !record(value.widget)) return null
  const widget = value.widget
  if (!nonBlank(widget.title) || !nonBlank(widget.code)) return null
  if (widget.kind !== 'svg' && widget.kind !== 'html' && !(typeof widget.kind === 'string' && (isChartKind(widget.kind)||isTableKind(widget.kind)))) return null
  return {
    title: widget.title.trim(),
    code: widget.code,
    kind: widget.kind,
    chars: typeof widget.chars === 'number' ? widget.chars : widget.code.length,
    loadingMessages: loadingMessages(widget.loading_messages),
  }
}

export function widgetPayload(block: ToolBlockState): WidgetPayload | null {
  if (block.name !== 'show_widget' || block.status !== 'ok') return null
  return parseWidgetPayload(block.display)
}

export function widgetPreview(block: ToolBlockState): WidgetPreview | null {
  if (block.name !== 'show_widget' || block.status !== 'running' || !record(block.input)) return null
  return {
    title: nonBlank(block.input.title) ? block.input.title.trim() : '',
    loadingMessages: loadingMessages(block.input.loading_messages),
  }
}

/** 下载文件名：标题转下划线并限制长度，扩展名按载体类型。 */
export function widgetFileName(payload: WidgetPayload): string {
  const base = payload.title.replace(/\s+/g, '_').replace(/[\\/:*?"<>|]/g, '').slice(0, 60) || 'widget'
  return `${base}.${isChartKind(payload.kind) ? payload.kind + '.json' : payload.kind}`
}
