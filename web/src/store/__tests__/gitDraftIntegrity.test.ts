import {expect,it,vi} from 'vitest'
import {createGitDraftStore,gitDraftScope} from '../gitDrafts'
const scope=gitDraftScope('owner','/fixture')
it.each(['not json','null','[]',JSON.stringify({[scope]:42}),JSON.stringify({[scope]:'x'.repeat(8001)})])('damaged draft %s is preserved through edits, retries and deletion',raw=>{
 const storage={read:vi.fn(()=>raw),write:vi.fn(()=>true),unpersisted:()=>false}
 const store=createGitDraftStore(storage)
 expect(store.getState().storageCorrupt).toBe(true);expect(store.getState().storageError).toBe(true)
 store.getState().setMessage(scope,'New draft 原文')
 expect(store.getState().byScope[scope]).toBe('New draft 原文')
 store.getState().retryStorage();expect(store.getState().storageError).toBe(true)
 store.getState().discardSession('owner');expect(store.getState().byScope[scope]).toBeUndefined()
 expect(storage.write).not.toHaveBeenCalled()
})
it('valid drafts keep their original values and ordinary persistence failures remain retryable',()=>{
 const raw=JSON.stringify({[scope]:'Original draft 原文'}),write=vi.fn().mockReturnValueOnce(false).mockReturnValue(true)
 const store=createGitDraftStore({read:()=>raw,write,unpersisted:()=>false})
 expect(store.getState().byScope[scope]).toBe('Original draft 原文');expect(store.getState().storageCorrupt).toBe(false)
 store.getState().setMessage(scope,'New draft 原文');expect(store.getState().storageError).toBe(true)
 store.getState().retryStorage();expect(store.getState().storageError).toBe(false)
 expect(write).toHaveBeenLastCalledWith('harness.git-drafts.v1',JSON.stringify({[scope]:'New draft 原文'}))
})
