import {app, Menu} from 'electron'
import assert from 'node:assert/strict'
import {execFile} from 'node:child_process'
import {promisify} from 'node:util'
import {setTimeout} from 'node:timers/promises'
import {fileURLToPath} from 'node:url'
import {configureAboutPanel} from '../../about-panel.mjs'
import {applicationMenu} from '../../application-menu.mjs'

app.setPath('userData', process.argv[2])
app.whenReady().then(async () => {
app.setName('Harness')
const iconPath = fileURLToPath(new URL('../../assets/harness-macos.png', import.meta.url))
app.dock.setIcon(iconPath)
configureAboutPanel(app, {iconPath})
for (const label of ['About Harness', '关于 Harness', 'About Harness']) {
  const menu = Menu.buildFromTemplate(applicationMenu({text: key => key === 'about' ? label : key, command: action => {
    if (action === 'about') app.showAboutPanel()
  }}))
  Menu.setApplicationMenu(menu)
  const item = menu.items[0].submenu.items.find(item => item.label === label)
  console.log("invoke-about")
  item.click()
  console.log("about-invoked")
  // 核对本进程实际可见的原生窗口，不以固定等待时间代替呈现回执。
  const script = `ObjC.import('CoreGraphics'); JSON.stringify(ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo(1,0))).filter(w => w.kCGWindowOwnerPID === ${process.pid} && w.kCGWindowLayer === 0))`
  const deadline = Date.now() + 5000
  let shown = false
  do {
    const {stdout} = await promisify(execFile)('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {timeout: 2000})
    shown = JSON.parse(stdout).some(window => window.kCGWindowIsOnscreen && window.kCGWindowBounds.Width > 0)
    if (!shown) await setTimeout(50)
  } while (!shown && Date.now() < deadline)
  assert.equal(shown, true, 'Native About panel must be visible')
}
console.log('native-about-opened:3')
app.exit(0)
}).catch(error => {console.error(error); app.exit(1)})
