import {afterEach, expect, it} from 'vitest'
import {renderToStaticMarkup} from 'react-dom/server'
import {DiffView} from '@/components/chat/blocks/DiffView'
import {setLocale, t} from '@/i18n'

afterEach(() => setLocale('zh-CN'))
it.each(['en', 'zh-CN'] as const)('renders full long source lines with bounded highlighting in %s', language => {
  setLocale(language)
  const before = 'a, '.repeat(20000), after = 'b, '.repeat(20000)
  const diff = `--- a/long.txt\n+++ b/long.txt\n@@ -1 +1 @@\n-${before}\n+${after}\n`
  const html = renderToStaticMarkup(<DiffView output={null} display={{diff}} path="long.txt" />)
  expect(html).toContain(before)
  expect(html).toContain(after)
  expect(html.match(/<mark /g)).toHaveLength(2)
  expect(html).toContain(t('ui.components.chat.blocks.DiffView.cbf9016bb88f'))
  expect(html).toContain('long.txt')
})
