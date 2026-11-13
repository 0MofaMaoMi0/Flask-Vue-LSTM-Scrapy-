// Authenticated transport only. Reading configuration never starts generation.
export class AssistantApiError extends Error {
 constructor(message:string,public readonly status:number,public readonly code:string,public readonly state?:string){super(message);this.name='AssistantApiError'}
}
const terminalStates=['DONE','FAILED','CANCELLED','REJECTED','EXPIRED']
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
 if(!r.ok||b.code!=='OK')throw new AssistantApiError(r.status===401?'请重新登录。':b.code==='REQUEST_ID_CONFLICT'?'此请求编号已使用，未再次调用模型。':b.code==='KEY_CHECK_FAILED'?'密钥检查失败，请检查模型、Key或服务商网络。':b.code==='INVALID_INPUT'?'配置或输入格式无效，请检查后再试。':'操作未完成：'+b.code,r.status,b.code,typeof b.data?.state==='string'?b.data.state:undefined)
 return b.data
}
