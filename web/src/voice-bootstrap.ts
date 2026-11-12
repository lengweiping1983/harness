import copy from './i18n/startup.generated.json'
import {initialLocale} from './i18n/initialLocale'
import './styles.css'

const root = document.getElementById('root')
if (!root) throw Error('Missing voice capture root')
const locale = initialLocale()
document.documentElement.lang = locale
const text = copy[locale]
function show(failed: boolean) {
  const frame = document.createElement('div')
  frame.className = 'flex min-h-dvh flex-col items-center justify-center gap-3 bg-surface p-4 text-xs text-muted'
  const message = document.createElement('p')
  message.setAttribute('role', failed ? 'alert' : 'status')
  message.textContent = text[failed ? 'ui.startup.failed' : 'ui.App.f76a8d866552']
  frame.append(message)
  if (failed) {
    const button = document.createElement('button')
    button.className = 'rounded-lg px-3 py-2 text-brand'
    button.textContent = text['ui.deferred.reload']
    button.onclick = () => window.location.reload()
    frame.append(button)
  }
  root!.replaceChildren(frame)
}
show(false)
const watchdog = setTimeout(() => show(true), 10000)
void import('./voice').catch(() => show(true)).finally(() => clearTimeout(watchdog))
