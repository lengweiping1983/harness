import {afterEach, expect, test} from 'vitest'
import {setLocale, t} from '@/i18n'
import {canvasBlob, ScreenshotError} from '../screenshot'
import {renderScreenshot} from '../screenshotAnnotations'
import {failureText} from '../resourceError'
import {noticeText} from '../noticeText'

afterEach(() => setLocale('zh-CN'))
test('PNG encoding failure retains identity across language changes without re-encoding', async () => {
  let attempts = 0
  const canvas = {toBlob(callback: BlobCallback) {attempts++; callback(null)}} as HTMLCanvasElement
  setLocale('en')
  const failure = await canvasBlob(canvas).catch(error => error)
  expect(failure).toBeInstanceOf(ScreenshotError)
  for (const language of ['en','zh-CN','en'] as const) {
    setLocale(language)
    expect(noticeText(failureText(failure))).toBe(t('ui.lib.screenshot.e15ffd56718a'))
    expect(attempts).toBe(1)
    expect(noticeText('Original 原文')).toBe('Original 原文')
  }
})

test('missing compositing context keeps translatable error identity without retry', () => {
  let attempts=0
  const canvas={width:4,height:4,getContext:()=>{attempts++;return null}} as unknown as HTMLCanvasElement
  const image={naturalWidth:4,naturalHeight:4} as HTMLImageElement
  let failure:unknown
  try {renderScreenshot(canvas,image,[])} catch(error) {failure=error}
  expect(failure).toBeInstanceOf(ScreenshotError)
  for(const language of ['en','zh-CN','en'] as const){
    setLocale(language)
    expect(noticeText(failureText(failure))).toBe(t('ui.imageActions.canvasUnavailable'))
    expect(attempts).toBe(1)
  }
})
