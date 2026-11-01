import {afterEach,expect,it} from 'vitest'
import {setLocale,t} from '@/i18n'
import type {GoalState} from '@/types'
import {goalActivity} from '../goalActivity'
afterEach(()=>setLocale('zh-CN'))
it.each(['constructor','toString','__proto__'])('unknown stop reason %s uses product fallback',stopReason=>{
 const state={activation:'disarmed',goal:{phase:'active',stopReason}} as unknown as GoalState
 const context={running:false,stopping:false,connected:true,queuePaused:false,queued:0,planMode:false,waiting:null} as const
 for(const locale of ['en','zh-CN'] as const){setLocale(locale);expect(goalActivity(state,context).note).toBe(t('ui.lib.goalActivity.b3b3fc7359ef'))}
})
