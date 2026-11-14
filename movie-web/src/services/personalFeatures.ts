import {aiWorkspace,resetAIWorkspace} from './aiWorkspace'
const errors:Record<string,string>={NOTE_VERSION_CONFLICT:'备注已被其他窗口更新，请重新打开后编辑。',PROFILE_VERSION_CONFLICT:'头像已被其他窗口更新，请刷新后重新保存。',PERSONAL_FEATURES_NOT_INSTALLED:'个人数据服务尚未安装，请稍后重试。',ANSWER_NOT_FOUND:'该回复尚未完成或不属于当前账户，不能保存反馈。'}
export async function personalRequest(path:string,data?:unknown){
 for(let attempt=0;attempt<2;attempt++){
  const headers:Record<string,string>={};const signal=AbortSignal.timeout(15000)
  if(data!==undefined){const c=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal});if(!c.ok)throw new Error('登录状态无法确认。');headers['X-CSRF-Token']=(await c.json()).data.csrf_token;headers['Content-Type']='application/json'}
  const r=await fetch('/api/v1/account/'+path,{credentials:'same-origin',signal,...(data!==undefined?{method:'POST',headers,body:JSON.stringify(data)}:{})});const body=await r.json()
  if(r.status===403&&body.code==='REQUEST_VERIFICATION_FAILED'&&attempt===0)continue
  if(!r.ok)throw new Error(errors[body.code]||(r.status===401?'登录已过期，请重新登录。':'保存或读取未完成：'+body.code))
  return body.data
 }
 throw new Error('请求验证已更新，请重试。')
}
export async function loadPersonalProfile(uid:number){
 resetAIWorkspace(uid)
 const p=await personalRequest('profile')
 if(aiWorkspace.owner===uid&&p.user_id===uid){aiWorkspace.avatar=p.avatar||'';aiWorkspace.profileVersion=p.version}
 return p
}
export async function persistAvatar(avatar:string|null){
 const uid=aiWorkspace.owner;const p=await personalRequest('profile',{avatar,version:aiWorkspace.profileVersion})
 if(aiWorkspace.owner===uid&&p.user_id===uid){aiWorkspace.avatar=p.avatar||'';aiWorkspace.profileVersion=p.version}
 return p
}
