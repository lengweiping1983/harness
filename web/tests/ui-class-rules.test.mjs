import {test} from 'node:test'
import assert from 'node:assert/strict'
import {classRuleErrors} from '../../scripts/ui_class_rules.mjs'

for(const token of ['px-[7px]','hover:gap-x-[3px]','md:-mt-[7px]','size-[17px]','rounded-ss-[4px]','inset-x-[5px]','-translate-x-[3px]','text-[length:15px]','[font-size:15px]','text-[15px]']){
 test('shared token rule covers '+token,()=>assert.ok(classRuleErrors(token,'fixture.tsx').length))
}
test('semantic classes, content property and explicit color opacity remain valid',()=>{
 assert.deepEqual(classRuleErrors('px-3 gap-x-2 size-4 max-h-dialog text-sm bg-brand/[0.12] [overflow-wrap:anywhere] text-[#fff]','fixture.tsx'),[])
})
test('undefined theme and invalid opacity keep their existing checks',()=>{
 assert.equal(classRuleErrors('hover:bg-hover','fixture.tsx').length,1)
 assert.equal(classRuleErrors('bg-brand/12','fixture.tsx').length,1)
})
