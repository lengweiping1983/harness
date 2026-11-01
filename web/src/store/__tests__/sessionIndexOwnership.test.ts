import {afterEach,expect,it} from 'vitest'
import {emptySession,useChatStore} from '../chat'
import {useChangeCards} from '../changeCards'

afterEach(()=>{
 useChatStore.setState({sessions:{}})
 useChangeCards.setState({choices:{},latest:{},target:null})
})
it.each(['__proto__','constructor','toString'])('an absent %s ID cannot resolve inherited session or change-card state',id=>{
 useChatStore.setState({sessions:{}})
 useChangeCards.setState({choices:{},latest:{},target:null})
 expect(useChatStore.getState().get(id)).toEqual(emptySession())
 expect(useChangeCards.getState().showLatest(id)).toBe(false)
 expect(useChangeCards.getState().target).toBeNull()
 useChatStore.getState().markLoading(id,true)
 const session=useChatStore.getState().get(id)
 expect(session.loading).toBe(true);expect(session.blocks).toEqual([])
 expect(Object.hasOwn(useChatStore.getState().sessions,id)).toBe(true)
 expect(Object.getPrototypeOf(useChatStore.getState().sessions)).toBe(Object.prototype)
 useChatStore.getState().drop(id)
 expect(useChatStore.getState().get(id)).toEqual(emptySession())
})
it('explicit own entries keep their identity even with inherited names',()=>{
 const session={...emptySession(),turn:7}
 useChatStore.setState({sessions:{['__proto__']:session}})
 expect(useChatStore.getState().get('__proto__')).toBe(session)
 expect(useChatStore.getState().get('constructor').turn).toBe(0)
})
