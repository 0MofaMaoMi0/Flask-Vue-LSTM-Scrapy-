import {reactive} from 'vue'

// UI-only state. Never put API keys or request payloads into browser storage.
export const aiWorkspace=reactive({owner:0,large:false,reduced:false,startFull:false,avatar:'',archives:{explore:[] as any[],floating:[] as any[]}})
export function resetAIWorkspace(owner:number){
 if(aiWorkspace.owner===owner)return
 aiWorkspace.owner=owner;aiWorkspace.avatar='';aiWorkspace.large=false;aiWorkspace.reduced=false;aiWorkspace.startFull=false
 aiWorkspace.archives.explore=[];aiWorkspace.archives.floating=[]
}
