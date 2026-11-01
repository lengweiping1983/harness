import {expect,it} from 'vitest'
import {createElement} from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
import {HookActivityList} from '@/components/chat/blocks/HookActivity'
it('an empty hook block has no group toggle or invalid first-row access',()=>{
 expect(renderToStaticMarkup(createElement(HookActivityList,{rows:[],sessionId:'fixture'}))).toBe('')
})
