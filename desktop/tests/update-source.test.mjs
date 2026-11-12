import assert from 'node:assert/strict'
import {test} from 'node:test'
import updateSource from '../update-source.cjs'
import {setupCopy} from '../setup-copy.mjs'
import {applicationMenu} from '../application-menu.mjs'

test('build source accepts public GitHub or HTTPS hosting, rejects implicit private source and credentials', () => {
  assert.deepEqual(updateSource({HARNESS_UPDATE_REPOSITORY:'owner/public-releases'}), {provider:'github', owner:'owner', repo:'public-releases', releaseType:'draft', vPrefixedTagName:true})
  assert.deepEqual(updateSource({HARNESS_UPDATE_URL:'https://downloads.example.com/harness'}), {provider:'generic', url:'https://downloads.example.com/harness/', channel:'latest'})
  for (const env of [{}, {HARNESS_UPDATE_REPOSITORY:'bad/path/extra'}, {HARNESS_UPDATE_URL:'https://user:secret@example.com/'}, {HARNESS_UPDATE_URL:'http://example.com/'}, {HARNESS_UPDATE_URL:'invalid'}, {HARNESS_UPDATE_URL:'https://example.com/?token=secret'}, {HARNESS_UPDATE_URL:'https://example.com/', HARNESS_UPDATE_REPOSITORY:'owner/repo'}]) assert.throws(() => updateSource(env))
})

test('same update state consumes English Chinese English menu resources without updater calls', () => {
  for (const language of ['en','zh-CN','en']) {
    const copy = setupCopy(language)
    assert.throws(() => updateSource({}, copy), error => error.message === copy('desktop.release.updateSourceRequired'))
    for (const platform of ['darwin','win32']) {
      const text = key => copy('ui.desktop.menu.' + key)
      const menus = applicationMenu({platform, text, command:() => {}, updateState:'downloading', updateProgress:42})
      const update = menus.find(menu => menu.label === text('help')).submenu[0]
      assert.equal(update.label, text('updateDownloadingMenu') + ' 42%')
      assert.equal(update.enabled, false)
      const ready = applicationMenu({platform, text, command:() => {}, updateState:'downloaded'}).find(menu => menu.label === text('help')).submenu[0]
      assert.equal(ready.label, text('updateReadyMenu'))
      assert.equal(ready.enabled, true)
      assert.ok(text('quitInstall'))
    }
  }
})
