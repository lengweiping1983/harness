import {afterEach, expect, it, vi} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {WindowsTitleBar} from '@/components/desktop/WindowsTitleBar'
import {setLocale} from '@/i18n'

afterEach(()=>vi.unstubAllGlobals())

it('leaves macOS, Linux and regular browser markup unchanged',()=>{
  for(const platform of [undefined,'darwin','linux']){
    vi.stubGlobal('window',platform?{harnessDesktop:{platform}}:{})
    expect(renderToStaticMarkup(<WindowsTitleBar />)).toBe('')
  }
})

it('reuses current menu resources in Windows without exposing window control replacements',async()=>{
  vi.stubGlobal('window',{harnessDesktop:{platform:'win32'}})
  for(const locale of ['en','zh-CN','en'] as const){
    await setLocale(locale)
    const markup=renderToStaticMarkup(<WindowsTitleBar />)
    expect(markup.match(/role="menuitem"/g)).toHaveLength(5)
    for(const text of locale==='en'?['File','Edit','View','Window','Help']:['文件','编辑','视图','窗口','帮助'])expect(markup).toContain('>'+text+'</button>')
    expect(markup).not.toMatch(/Minimize|Maximize|关闭窗口/)
    expect(markup).not.toContain('windows-titlebar-name')
    expect(markup).not.toContain('>Harness</span>')
  }
})
