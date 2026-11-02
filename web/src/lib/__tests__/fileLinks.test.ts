import { describe, expect, it } from 'vitest'
import { isHtmlPath, previewUrl } from '@/lib/fileLinks'

describe('previewUrl', () => {
  it('keeps the folder in the path so relative assets resolve', () => {
    expect(previewUrl('site/index.html', { root: null })).toBe('/api/v1/preview/site/index.html')
    expect(previewUrl('site/index.html', { root: '/work/app' })).toBe('/api/v1/preview/site/index.html?root=%2Fwork%2Fapp')
  })

  it('marks workspace-root pages and escapes odd names', () => {
    expect(previewUrl('index.html', { root: null })).toBe('/api/v1/preview/__root__/index.html')
    expect(previewUrl('站点/首页.html', { root: null })).toBe(
      `/api/v1/preview/${encodeURIComponent('站点')}/${encodeURIComponent('首页.html')}`,
    )
  })

  it('normalizes windows separators', () => {
    expect(previewUrl('site\\index.html', { root: null })).toBe('/api/v1/preview/site/index.html')
  })
})

describe('isHtmlPath', () => {
  it('recognizes html documents only', () => {
    expect(isHtmlPath('site/index.html')).toBe(true)
    expect(isHtmlPath('page.htm')).toBe(true)
    expect(isHtmlPath('page.xhtml')).toBe(true)
    expect(isHtmlPath('styles.css')).toBe(false)
    expect(isHtmlPath('template.html.erb')).toBe(false)
  })
})
