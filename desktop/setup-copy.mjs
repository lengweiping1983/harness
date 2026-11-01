import {execFileSync} from 'node:child_process'
import {readFileSync} from 'node:fs'

function localeRegion(value) {
  return value.split('.')[0].split('@')[0].split(/[-_]/).slice(1).find(part => /^[a-z]{2}$/i.test(part))?.toUpperCase()
}

export function setupLanguage(language, {env = process.env, platform = process.platform, execute = execFileSync} = {}) {
  if (language === 'en' || language === 'zh-CN') return language
  let region
  const options = {encoding: 'utf8', timeout: 1000, maxBuffer: 16384, windowsHide: true, env}
  try {
    if (platform === 'darwin') {
      region = localeRegion(execute('/usr/bin/defaults', ['read', '-g', 'AppleLocale'], options).trim())
    } else if (platform === 'win32') {
      const script = `Add-Type -TypeDefinition 'using System;using System.Text;using System.Runtime.InteropServices;public class HarnessGeo{[DllImport("kernel32.dll",CharSet=CharSet.Unicode)]public static extern int GetUserDefaultGeoName(StringBuilder name,int count);}' ; $name=New-Object System.Text.StringBuilder 16; if([HarnessGeo]::GetUserDefaultGeoName($name,16)){[Console]::Write($name.ToString())}`
      const value = execute('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', script], options).trim().toUpperCase()
      region = /^[A-Z]{2}$/.test(value) ? value : undefined
    } else {
      const value = env.LC_ALL || env.LC_TIME || env.LANG
      if (value) region = localeRegion(value)
    }
  } catch { /* 系统地区不可用时沿后端规则选择英文。 */ }
  return ['CN', 'HK', 'MO', 'TW'].includes(region) ? 'zh-CN' : 'en'
}

export function setupCopy(language = process.env.HARNESS_LANGUAGE, options) {
  const locale = setupLanguage(language, options)
  const catalog = JSON.parse(new TextDecoder('utf-8', {fatal:true}).decode(readFileSync(
    new URL('../harness/i18n/locales/' + locale + '.json', import.meta.url))))
  return (key, params = {}) => catalog[key].replace(/\{(\w+)\}/g,
    (match, name) => Object.hasOwn(params, name) ? String(params[name]) : match)
}
