import assert from 'node:assert/strict'
import {test} from 'node:test'
import {attachApplicationPermissions} from '../application-permissions.mjs'

function fixture() {
  const origin='http://127.0.0.1:8765'
  const contents=()=>({mainFrame:{url:origin+'/'},isDestroyed:()=>false})
  const window={webContents:contents()},voiceWindow={webContents:contents()}
  const handlers={}
  attachApplicationPermissions({setPermissionCheckHandler:fn=>{handlers.check=fn},setPermissionRequestHandler:fn=>{handlers.request=fn}},()=>({origin,window,voiceWindow}))
  const request=(wc,permission,details={requestingUrl:origin+'/',isMainFrame:true})=>{
    let allowed;handlers.request(wc,permission,value=>{allowed=value},details);return allowed
  }
  return {origin,window,voiceWindow,handlers,request,contents}
}
test('application clipboard write is granted in both Electron callbacks, without allowing clipboard reads or web frames',()=>{
  const {origin,window,voiceWindow,handlers,request,contents}=fixture(),wc=window.webContents
  assert.equal(handlers.check(wc,'clipboard-sanitized-write',origin,{isMainFrame:true}),true)
  assert.equal(request(wc,'clipboard-sanitized-write'),true)
  for(const permission of ['clipboard-read','deprecated-sync-clipboard-read','unknown']) {
    assert.equal(handlers.check(wc,permission,origin,{}),false);assert.equal(request(wc,permission),false)
  }
  for(const other of [null,voiceWindow.webContents,contents()]) {
    assert.equal(handlers.check(other,'clipboard-sanitized-write',origin,{}),false)
    assert.equal(request(other,'clipboard-sanitized-write'),false)
  }
  for(const details of [{isMainFrame:false,requestingUrl:origin+'/'},{isMainFrame:true,requestingUrl:'https://example.com/'},{isMainFrame:true,requestingUrl:'invalid'}]) {
    assert.equal(handlers.check(wc,'clipboard-sanitized-write',origin,details),false)
    assert.equal(request(wc,'clipboard-sanitized-write',details),false)
  }
  wc.mainFrame.url='https://example.com/'
  assert.equal(request(wc,'clipboard-sanitized-write'),false)
  wc.mainFrame.url=origin+'/';wc.isDestroyed=()=>true
  assert.equal(request(wc,'clipboard-sanitized-write'),false)
})
test('same live permission policy preserves main and voice audio access and rejects camera or unrelated origins',()=>{
  const {origin,window,voiceWindow,handlers,request}=fixture()
  for(const wc of [window.webContents,voiceWindow.webContents]) {
    assert.equal(handlers.check(wc,'media',origin,{mediaTypes:['audio']}),true)
    assert.equal(request(wc,'media',{requestingUrl:origin+'/',mediaTypes:['audio']}),true)
    assert.equal(handlers.check(wc,'media',origin,{mediaTypes:['video']}),false)
    assert.equal(handlers.check(wc,'media','https://example.com/',{mediaTypes:['audio']}),false)
  }
})
