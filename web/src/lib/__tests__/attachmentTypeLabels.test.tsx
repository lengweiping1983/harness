import {renderToStaticMarkup} from 'react-dom/server'
import {afterEach, describe, expect, it} from 'vitest'
import {setLocale, t} from '@/i18n'
import {AttachmentDraftTrack} from '@/components/chat/AttachmentDraft'

afterEach(() => setLocale('zh-CN'))

describe('附件媒体类型标签', () => {
  it('未知子类型不读取继承属性，普通类型仍按当前语言显示', () => {
    for (const locale of ['en', 'zh-CN', 'en'] as const) {
      setLocale(locale)
      for (const [subtype, label] of [
        ['__proto__', '__PROTO__'], ['constructor', 'CONSTRUCTOR'],
        ['toString', 'TOSTRING'], ['x-original', 'X-ORIGINAL'],
        ['json', 'JSON'], ['plain', t('ui.components.chat.AttachmentDraft.dd81861811e5')],
      ]) {
        const html = renderToStaticMarkup(<AttachmentDraftTrack items={[{
          localId:'fixture', name:'Original 文件.dat', mediaType:`application/${subtype}`,
          size:3, isImage:false, attachmentId:'fixture',
        }]} onRemove={() => undefined} />)
        expect(html).toContain(label!)
        expect(html).toContain('Original 文件.dat')
      }
    }
  })
})
