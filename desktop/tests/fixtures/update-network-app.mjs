import assert from 'node:assert/strict'
import {app,session} from 'electron'
import {createUpdateFetch} from '../../update-network.mjs'
app.setPath('userData',process.argv[2])
app.whenReady().then(async()=>{
  app.dock?.hide()
  const network=session.fromPartition('update-network-test',{cache:false})
  await network.setProxy({mode:'fixed_servers',proxyRules:`http=127.0.0.1:${process.argv[3]}`})
  const fetch=createUpdateFetch(network,{directFetch:()=>assert.fail('A configured proxy must not be bypassed')})
  const response=await fetch('http://harness-updates.invalid/latest',{signal:AbortSignal.timeout(5000)})
  assert.equal(response.status,200)
  assert.deepEqual(await response.json(),{version:'test-proxy'})
  console.log('update-proxy-session-passed')
  app.quit()
}).catch(error=>{console.error(error);app.exit(1)})
