import { expect, it } from 'vitest'
import { parseWidgetPayload, widgetFileName, widgetPayload, widgetPreview } from '../widgetPayload'
import type { ToolBlockState } from '@/types'

const SVG = '<svg viewBox="0 0 680 120" width="100%"></svg>'

function block(patch: Partial<ToolBlockState>): ToolBlockState {
  return { kind: 'tool', id: 'b1', at: 1, callId: 'c1', name: 'show_widget', status: 'ok', ...patch } as ToolBlockState
}

it('parses a well-formed display payload', () => {
  const payload = parseWidgetPayload({ widget: { title: ' 架构 ', code: SVG, kind: 'svg', chars: 40, loading_messages: [' a ', '', 'b'] } })
  expect(payload).toEqual({ title: '架构', code: SVG, kind: 'svg', chars: 40, loadingMessages: ['a', 'b'] })
})

it('rejects malformed payloads without guessing', () => {
  expect(parseWidgetPayload(null)).toBeNull()
  expect(parseWidgetPayload({})).toBeNull()
  expect(parseWidgetPayload({ widget: { title: 't' } })).toBeNull()
  expect(parseWidgetPayload({ widget: { title: ' ', code: SVG, kind: 'svg' } })).toBeNull()
  expect(parseWidgetPayload({ widget: { title: 't', code: '', kind: 'svg' } })).toBeNull()
  expect(parseWidgetPayload({ widget: { title: 't', code: SVG, kind: 'png' } })).toBeNull()
})

it('binds payloads to completed show_widget calls only', () => {
  const display = { widget: { title: 't', code: SVG, kind: 'svg' } }
  expect(widgetPayload(block({ display }))?.title).toBe('t')
  expect(widgetPayload(block({ display, status: 'running' }))).toBeNull()
  expect(widgetPayload(block({ display, status: 'error' }))).toBeNull()
  expect(widgetPayload(block({ display, name: 'read_image' }))).toBeNull()
  expect(widgetPayload(block({}))).toBeNull()
})

it('exposes running previews from parsed input only', () => {
  const preview = widgetPreview(block({ status: 'running', input: { title: ' 图 ', loading_messages: ['one', 'two'] } }))
  expect(preview).toEqual({ title: '图', loadingMessages: ['one', 'two'] })
  expect(widgetPreview(block({ status: 'ok', input: { title: 'x' } }))).toBeNull()
  expect(widgetPreview(block({ status: 'running' }))).toBeNull()
  expect(widgetPreview(block({ status: 'running', name: 'bash', input: { title: 'x' } }))).toBeNull()
})

it('derives safe download file names', () => {
  const svgPayload = parseWidgetPayload({ widget: { title: 'Agent 核心架构', code: SVG, kind: 'svg' } })!
  expect(widgetFileName(svgPayload)).toBe('Agent_核心架构.svg')
  const htmlPayload = parseWidgetPayload({ widget: { title: 'a/b:c*d', code: '<div>x</div>', kind: 'html' } })!
  expect(widgetFileName(htmlPayload)).toBe('abcd.html')
  const blank = parseWidgetPayload({ widget: { title: ' ', code: SVG, kind: 'svg' } })
  expect(blank).toBeNull()
})
