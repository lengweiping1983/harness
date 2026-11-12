import assert from 'node:assert/strict'
import {app,session,Menu} from 'electron'
import {createServer} from 'node:http'
import {createHash} from 'node:crypto'
import {applicationMenu,refreshUpdateMenu} from '../../application-menu.mjs'
import {downloadInstaller} from '../../mac-installer.mjs'
import {InstallerUpdates} from '../../installer-updates.mjs'
import {createUpdateFetch} from '../../update-network.mjs'
import {setupCopy} from '../../setup-copy.mjs'
app.setPath('userData',process.argv[2])
app.whenReady().then(async()=>{
  app.dock?.hide()
  const bytes=Buffer.alloc(2*1024*1024,7),copy=setupCopy('en'),text=key=>copy('ui.desktop.menu.'+key)
  let response,observed=false,menu,item
  const server=createServer((_request,result)=>{
    response=result
    result.writeHead(200,{'Content-Type':'application/octet-stream','Content-Length':bytes.length})
    result.write(bytes.subarray(0,8192))
    // 剩余正文只有在消费端已观察到真实分块和原生菜单更新后才发送。
  })
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
  try {
    const network=createUpdateFetch(session.fromPartition('progress-test',{cache:false}))
    const installer={prepare:async(asset,_version,progress)=>downloadInstaller({fetch:(_url,options)=>network(`http://127.0.0.1:${server.address().port}/installer`,options),asset,directory:process.argv[2],signal:AbortSignal.timeout(5000),progress})}
    const updates=new InstallerUpdates({source:{provider:'github',owner:'owner',repo:'repo'},version:'1.0.0',packaged:true,installer,notify:()=>{
      refreshUpdateMenu(menu,updates.snapshot(),text)
      if(updates.received===8192&&!observed){
        observed=true
        assert.equal(Menu.getApplicationMenu(),menu)
        assert.equal(menu.getMenuItemById('harness-updates'),item)
        assert.ok(item.label.includes('0.4%'));assert.ok(item.label.includes('8.0 KB'))
        assert.equal(updates.state,'downloading');assert.equal(response.writableEnded,false)
        response.end(bytes.subarray(8192))
      }
    }})
    updates.state='available';updates.version='1.1.0';updates.asset={size:bytes.length,digest:'sha256:'+createHash('sha256').update(bytes).digest('hex'),browser_download_url:'https://github.com/owner/repo/installer.dmg'}
    menu=Menu.buildFromTemplate(applicationMenu({text,command:()=>{}}));Menu.setApplicationMenu(menu);item=menu.getMenuItemById('harness-updates')
    await updates.download()
    assert.equal(observed,true);assert.equal(updates.state,'downloaded');assert.equal(item.label,text('updateReadyMenu'))
    console.log('native-streaming-update-progress-passed')
  } finally {
    server.closeAllConnections()
    await new Promise((resolve,reject)=>server.close(error=>error?reject(error):resolve()))
  }
  app.quit()
}).catch(error=>{console.error(error);app.exit(1)})
