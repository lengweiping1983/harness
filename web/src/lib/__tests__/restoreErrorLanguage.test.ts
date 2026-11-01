import {afterEach, expect, test, vi} from 'vitest'
import {setLocale, t} from '@/i18n'
import {readRestorePreview} from '../fileRestore'
import {ApiError} from '../http'
import {noticeText} from '../noticeText'
import type {NetChangedFile} from '../protocol.generated'

afterEach(()=>{vi.unstubAllGlobals();setLocale('zh-CN')})
test('invalid restore identity preserves resource and never reads or writes',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch)
 setLocale('en')
 const file={first_seq:7,last_seq:9,path:'Original.txt'} as NetChangedFile
 const failure=await readRestorePreview({sessionId:'fixture',turn:0,seq:1},file,9).catch(error=>error)
 expect(failure).toBeInstanceOf(ApiError)
 expect(failure.status).toBe(409);expect(failure.code).toBe('restore/invalid-target')
 for(const language of ['en','zh-CN','en'] as const){setLocale(language);expect(noticeText(failure.failure.copy)).toBe(t('ui.lib.fileRestore.84ec5178e924'))}
 expect(fetch).not.toHaveBeenCalled();expect(file.path).toBe('Original.txt')
})
