import {expect, it} from 'vitest'
import {newSessionDirectory} from '../workspaces'

const projects = [{id:'project',path:'/project/B',name:'User project 原文',color:'blue'}]
it('uses the selected project rather than the host startup directory',()=>{
 expect(newSessionDirectory(projects,'project','/host/A')).toBe('/project/B')
})
it.each([null,'missing'])('falls back only when selected project %s is unavailable',id=>{
 expect(newSessionDirectory(projects,id,'/host/A')).toBe('/host/A')
})
it('does not invent a directory when neither source exists',()=>{
 expect(newSessionDirectory([],null,undefined)).toBeUndefined()
})
