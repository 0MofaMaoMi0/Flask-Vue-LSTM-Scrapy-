export type User = {id:number;username:string;is_admin?:boolean}
export class AuthError extends Error { constructor(public status:number,message:string){super(message)} }
let csrfToken = ''
let unauthorized:()=>void=()=>{}
export function onUnauthorized(fn:()=>void){unauthorized=fn}
export async function call<T>(path:string,method='GET',data?:unknown,signal?:AbortSignal):Promise<T>{
 const timeout=AbortSignal.timeout(30000)
 let response:Response
 try{response=await fetch(`/api/v1/auth/${path}`,{method,credentials:'same-origin',headers:{...(method==='POST'?{'Content-Type':'application/json','X-CSRF-Token':csrfToken}:{})},...(data!==undefined?{body:JSON.stringify(data)}:{}),signal:signal?AbortSignal.any([signal,timeout]):timeout})}
 catch{throw new Error(timeout.aborted?'请求超时，请稍后重试':'网络异常，请检查连接后重试')}
 let body;try{body=await response.json()}catch{throw new Error('服务响应异常，请稍后重试')}
 if(response.status===401)unauthorized()
 if(!response.ok||body?.code!==0){
  if(response.status===403&&method==='POST') {csrfToken='';try{await refreshCsrf()}catch{} }
  const messages:Record<number,string>={400:'请检查输入信息，密码需为 6–128 个字符',401:'未登录或账号密码不正确',403:'请求验证失效，请重新提交',409:'注册信息冲突，请更换用户名',429:'操作过于频繁，请稍后重试'}
  // Display-only mapping of the review/verification codes; token, CSRF and login flow stay unchanged.
  const forbidden:Record<string,string>={REVIEW_READ_ONLY:'当前是只读验收环境，该写入已被拒绝，未产生任何改动。',REQUEST_VERIFICATION_FAILED:'请求验证已失效，请重新提交。',CSRF_INVALID:'请求验证已失效，请重新提交。'}
  const code=typeof body?.code==='string'?body.code:''
  const message=response.status===403?(forbidden[code]||messages[403]):(messages[response.status]||'服务异常，请稍后重试')
  throw new AuthError(response.status,message)
 }
 return body.data as T
}
export async function refreshCsrf(){csrfToken=(await call<{csrf_token:string}>('csrf')).csrf_token}
export function clearCsrf(){csrfToken=''}
export async function request<T>(action:'login'|'register',data:Record<string,string>,signal?:AbortSignal):Promise<T>{
 await refreshCsrf();return call<T>(action,'POST',data,signal)
}
