import assert from 'node:assert/strict'
import {test} from 'node:test'
import {setupCopy} from '../setup-copy.mjs'
import {updateDialogOptions} from '../update-dialogs.mjs'
import {InstallerUpdates} from '../installer-updates.mjs'

test('one prepared update renders actual dialog options English Chinese English without checking or installing again',()=>{
  const updates=new InstallerUpdates({source:{provider:'github',owner:'owner',repo:'repo'},version:'1.0.0',packaged:true,fetch:assert.fail,installer:{install:assert.fail}})
  updates.version='1.1.0';updates.state='downloaded';updates.progress=100
  const shown=[]
  const keys=['updateMacDownloaded','updateDownloaded','quitInstall','later','updateMacInstallerDetail','updateInstallerDetail','updateAvailable','downloadUpdate','updateInstallFailed','updateIntegrityFailed','updateInstallLocation']
  for(const language of ['en','zh-CN','en']) {
    const copy=setupCopy(language),text=Object.fromEntries(keys.map(key=>[key,copy('ui.desktop.menu.'+key)]))
    assert.ok(Object.values(text).every(value=>value.length<=120))
    const ready=updateDialogOptions(updates.snapshot(),true,text)
    assert.equal(ready.message,text.updateMacDownloaded)
    assert.deepEqual(ready.buttons,[text.quitInstall,text.later]);assert.equal(ready.cancelId,1)
    const available=updateDialogOptions({...updates.snapshot(),state:'available'},true,text)
    assert.equal(available.detail,text.updateMacInstallerDetail.replace('{version}','1.1.0'))
    assert.equal(updateDialogOptions({...updates.snapshot(),state:'available'},false,text).detail,text.updateInstallerDetail.replace('{version}','1.1.0'))
    shown.push(ready.message)
  }
  assert.equal(shown[0],shown[2]);assert.notEqual(shown[0],shown[1]);assert.equal(updates.state,'downloaded')
})
