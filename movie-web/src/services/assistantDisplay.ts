import {aiWorkspace} from './aiWorkspace'
export const CLEAR_ASSISTANT_DISPLAY='mi2-clear-assistant-display'
export type AssistantSurface='explore'|'floating'
export function clearAssistantDisplay(surface:AssistantSurface){
 const detail={surface,owner:aiWorkspace.owner,busy:false}
 window.dispatchEvent(new CustomEvent(CLEAR_ASSISTANT_DISPLAY,{detail}))
 if(detail.busy)return false
 aiWorkspace.archives[surface]=[]
 return true
}
