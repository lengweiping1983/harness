import {afterEach,expect,it,vi} from 'vitest'
import {DEFAULT_FONT_SIZE,useAppStore} from '../app'

afterEach(()=>{vi.unstubAllGlobals();useAppStore.setState({uiFontSize:DEFAULT_FONT_SIZE})})
it.each([[9,10],[23,22],[12.6,13],[10,10],[22,22],[NaN,16],[Infinity,16],[-Infinity,16]])('font size %s keeps store DOM and persistence at %s',(input,expected)=>{
 const style={fontSize:''},setItem=vi.fn()
 vi.stubGlobal('document',{documentElement:{style}})
 vi.stubGlobal('localStorage',{setItem})
 useAppStore.getState().setUiFontSize(input)
 expect(useAppStore.getState().uiFontSize).toBe(expected)
 expect(style.fontSize).toBe(`${expected}px`)
 expect(setItem).toHaveBeenCalledWith('harness.ui-font-size',String(expected))
})
