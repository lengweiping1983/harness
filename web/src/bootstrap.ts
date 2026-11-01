import copy from './i18n/startup.generated.json'
import {initialLocale} from './i18n/initialLocale'
import {loadApp} from './lib/appLoader'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw new Error('Missing root element')
const locale = initialLocale()
document.documentElement.lang = locale
const text = copy[locale]
function show(failed: boolean) {
  const frame = document.createElement('div')
  frame.className = 'flex h-dvh flex-col items-center justify-center gap-3 bg-canvas p-4 text-sm text-muted'
  const message = document.createElement('p')
  message.setAttribute('role', failed ? 'alert' : 'status')
  message.textContent = text[failed ? 'ui.startup.failed' : 'ui.App.f76a8d866552']
  frame.append(message)
  if (failed) {
    const reload = document.createElement('button')
    reload.type = 'button'
    reload.className = 'rounded-lg px-3 py-2 text-brand hover:bg-raised focus-visible:outline-brand'
    reload.textContent = text['ui.deferred.reload']
    reload.onclick = () => window.location.reload()
    frame.append(reload)
  }
  root!.replaceChildren(frame)
}
show(false)
const watchdog = setTimeout(() => show(true), 10000)
// 只预取主页面代码；共享页仍按其独立入口加载。失败由页面边界呈现。
if (window.location.pathname !== '/shared') void loadApp().catch(() => {})
// 超时只结束等待提示；迟到入口仍可接管页面，不重放任何业务写入。
void import('./main').catch(() => show(true)).finally(() => clearTimeout(watchdog))
