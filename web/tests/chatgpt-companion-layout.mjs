import assert from 'node:assert/strict'

export async function assertCompanionLayout(page, view) {
 const layout=await page.evaluate(()=>{
  const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height}}
  const visible=node=>node.getClientRects().length>0
  const target=document.querySelector('#target-session')
  const continuous=document.querySelector('#continuous')
  return {
   view:document.documentElement.dataset.view,width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
   rootWidth:document.documentElement.getBoundingClientRect().width,
   expandVisible:visible(document.querySelector('#open-tab')),
   controls:[...document.querySelectorAll('input,select,button')].filter(visible).map(node=>({
    id:node.id,...rect(node),clipped:node.tagName==='BUTTON'&&node.scrollWidth>node.clientWidth,
   })),
   columns:getComputedStyle(document.querySelector('.panels')).gridTemplateColumns.split(' ').length,
   targetClipped:target.scrollHeight>target.clientHeight,
   continuous:{...rect(continuous.closest('label')),named:continuous.labels?.length===1,
    checkbox:continuous.type==='checkbox'},
  }
 })
 assert.equal(layout.view,view)
 assert.ok(layout.scrollWidth<=layout.width,JSON.stringify(layout))
 assert.equal(layout.expandVisible,view==='popup')
 if(view==='popup')assert.equal(layout.rootWidth,760)
 assert.equal(layout.columns,layout.width<=640?1:2)
 assert.equal(layout.targetClipped,false)
 assert.equal(layout.continuous.named,true)
 assert.equal(layout.continuous.checkbox,true)
 assert.ok(layout.continuous.left>=0&&layout.continuous.right<=layout.width)
 for(const control of layout.controls){
  assert.ok(control.left>=0&&control.right<=layout.width,JSON.stringify(control))
  assert.equal(control.clipped,false,control.id)
  if(['endpoint','token','session','connect','start','stop'].includes(control.id))assert.ok(control.height>=40,control.id)
 }
 return layout
}
