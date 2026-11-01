import {test} from 'node:test'
import assert from 'node:assert/strict'
import {cssFontErrors} from '../../scripts/ui_css_rules.mjs'

for (const declaration of ['font-size:15px', 'font-size : 15px', 'FONT-SIZE:15PX', 'font-size:/* original */15px', 'font-size:0.9375rem']) {
  test('reject non-scale CSS declaration: '+declaration,()=>{
    assert.equal(cssFontErrors('.original {'+declaration+'}', 'original.css').length,1)
  })
}
test('keep shared scale, relative content typography, and variable sizing',()=>{
  for(const value of ['12px','13px','14px','16px','20px','28px','.75rem','1rem','1.06em','var(--text-compact)']){
    assert.deepEqual(cssFontErrors('.original{font-size:'+value+'}', 'original.css'),[])
  }
})
test('screenshot dimension exception belongs only to its exact source and selector',()=>{
  assert.deepEqual(cssFontErrors('.screenshot-size {font-size:11px}', 'components/chat/screenshot.css'),[])
  assert.equal(cssFontErrors('.other {font-size:11px}', 'components/chat/screenshot.css').length,1)
  assert.equal(cssFontErrors('.screenshot-size {font-size:11px}', 'other.css').length,1)
})
test('comments do not create declarations and broken CSS is not accepted',()=>{
  assert.deepEqual(cssFontErrors('/* font-size:15px */.original{color:red}', 'original.css'),[])
  assert.throws(()=>cssFontErrors('.original {', 'original.css'))
})
