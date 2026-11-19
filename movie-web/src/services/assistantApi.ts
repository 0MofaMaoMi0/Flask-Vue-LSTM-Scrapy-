// Authenticated transport only. Reading configuration never starts generation.
export class AssistantApiError extends Error {
 constructor(message:string,public readonly status:number,public readonly code:string,public readonly state?:string){super(message);this.name='AssistantApiError'}
}
const terminalStates=['DONE','FAILED','CANCELLED','REJECTED','EXPIRED']
const terminalMessages:Record<string,string>={
 USER_REQUEST_IN_PROGRESS:'同一账户已有生成任务在运行，本次请求未自动重试，也不会取消另一个窗口的任务。',
 RATE_LIMITED:'当前生成请求过多，本次请求未自动重试，请稍后再试。',
 REQUEST_PENDING:'上一个生成任务尚未结束，本次请求未重复提交。',
 REQUEST_INTERRUPTED:'上次生成在服务重启时中断，结果未确认，未自动重试。',
 ASSISTANT_UNAVAILABLE:'生成服务暂时不可用，结果未确认，未自动重试。',
 MOVIE_NOT_FOUND:'请求的电影不在当前目录中。',
 CANCELLED:'本次生成已取消，未自动重试。',
 CONVERSATION_UNAVAILABLE:'对话已失效，请开始新对话。',
 REQUEST_ID_CONFLICT:'此请求编号已使用，未再次调用模型。',
 REQUEST_EXPIRED:'该请求已过期，结果未确认，未自动重试。',
 REVIEW_READ_ONLY:'当前是只读验收环境，该写入已被拒绝，未产生任何改动。',
 REQUEST_VERIFICATION_FAILED:'请求验证已失效，本次未提交。'
}
// Shared Chinese wording for generation conflicts on both the HTTP and the SSE terminal path.
export function assistantConflictMessage(code:string,state?:string):string{
 if(terminalMessages[code])return terminalMessages[code]
 if(terminalStates.includes(state||''))return '本次生成未完成（'+code+'），未自动重试。'
 return '操作未完成：'+code
}
export function diagnosticRequestAfterError(requestId:string,error:unknown):string|null{
 if(error instanceof AssistantApiError&&(terminalStates.includes(error.state||'')||[400,401,403,404,429].includes(error.status)||error.code==='REQUEST_ID_CONFLICT'))return null
 return requestId
}
export async function assistantRequest(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 const headers:Record<string,string>={}
 if(method!=='GET'){
  const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal});if(!r.ok)throw new Error('登录状态已失效，请重新登录。')
  headers['X-CSRF-Token']=(await r.json()).data.csrf_token;headers['Content-Type']='application/json'
 }
 return fetch('/api/v1/assistant/'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),signal})
}
export async function assistantJson(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 const r=await assistantRequest(path,method,body,signal);const b=await r.json()
 if(!r.ok||b.code!=='OK')throw new AssistantApiError(r.status===401?'请重新登录。':b.code==='KEY_CHECK_FAILED'?'密钥检查失败，请检查模型、Key或服务商网络。':b.code==='INVALID_INPUT'?'配置或输入格式无效，请检查后再试。':assistantConflictMessage(b.code,typeof b.data?.state==='string'?b.data.state:undefined),r.status,b.code,typeof b.data?.state==='string'?b.data.state:undefined)
 return b.data
}
