export type User = {id:number;username:string;is_admin?:boolean}
export type AuthThrottle={retryAfterSeconds?:number;retryAfterAt?:number;retryAfterSource?:'seconds'|'http-date';message:string;source:string}
export class AuthError extends Error {
 retryAfterSeconds?:number
 retryAfterAt?:number
 retryAfterSource?:'seconds'|'http-date'
 constructor(public status:number,message:string,meta:{retryAfterSeconds?:number;retryAfterAt?:number;retryAfterSource?:'seconds'|'http-date'}={}){
  super(message);this.name='AuthError';this.retryAfterSeconds=meta.retryAfterSeconds;this.retryAfterAt=meta.retryAfterAt;this.retryAfterSource=meta.retryAfterSource
 }
}
let csrfToken = ''
let lastThrottle:AuthThrottle|null=null
const MAX_RETRY_AFTER_SECONDS=24*60*60
let unauthorized:()=>void=()=>{}
function retryAfter(value:string|null,now=Date.now()){
 const raw=value?.trim()
 if(raw&&/^\d+$/.test(raw)){
  const seconds=Number(raw)
  if(Number.isFinite(seconds)&&seconds>=0){
   // Keep a syntactically valid but absurdly large value from producing an
   // infinite deadline/permanent cooldown; 24h is a finite defensive cap.
   const safeSeconds=Math.min(seconds,MAX_RETRY_AFTER_SECONDS)
   return {retryAfterSeconds:safeSeconds,retryAfterAt:now+safeSeconds*1000,retryAfterSource:'seconds' as const}
  }
 }
 if(raw){
  const at=Date.parse(raw)
  if(Number.isFinite(at))return {retryAfterSeconds:Math.max(0,Math.ceil((at-now)/1000)),retryAfterAt:at,retryAfterSource:'http-date' as const}
 }
 return {}
}
export function getAuthThrottle():AuthThrottle|null{
 if(lastThrottle?.retryAfterAt!==undefined&&lastThrottle.retryAfterAt<=Date.now())return null
 return lastThrottle
}
export function onUnauthorized(fn:()=>void){unauthorized=fn}
export async function call<T>(path:string,method='GET',data?:unknown,signal?:AbortSignal):Promise<T>{
 const timeout=AbortSignal.timeout(30000)
 let response:Response
 try{response=await fetch(`/api/v1/auth/${path}`,{method,credentials:'same-origin',headers:{...(method==='POST'?{'Content-Type':'application/json','X-CSRF-Token':csrfToken}:{})},...(data!==undefined?{body:JSON.stringify(data)}:{}),signal:signal?AbortSignal.any([signal,timeout]):timeout})}
 catch{throw new Error(timeout.aborted?'请求超时，请稍后重试':'网络异常，请检查连接后重试')}
 let body;try{body=await response.json()}catch{throw new Error('服务响应异常，请稍后重试')}
 const throttle=response.status===429?retryAfter(response.headers.get('Retry-After')):{}
 if(response.status===429)lastThrottle={...throttle,message:'操作过于频繁，请稍后重试',source:path}
 else if(response.ok&&body?.code===0)lastThrottle=null
 if(response.status===401)unauthorized()
 if(!response.ok||body?.code!==0){
  if(response.status===403&&method==='POST') {csrfToken='';try{await refreshCsrf()}catch{} }
  const messages:Record<number,string>={400:'请检查输入信息（用户名 3–32 位，密码 6–128 个字符）',401:'未登录或账号密码不正确',403:'请求验证失效，请重新提交',409:'注册信息冲突，请更换用户名',429:'操作过于频繁，请稍后重试'}
  // Display-only mapping of the review/verification codes; token, CSRF and login flow stay unchanged.
  const forbidden:Record<string,string>={REVIEW_READ_ONLY:'当前是只读验收环境，该写入已被拒绝，未产生任何改动。',REQUEST_VERIFICATION_FAILED:'请求验证已失效，请重新提交。',CSRF_INVALID:'请求验证已失效，请重新提交。','40310':'请求验证已失效，请重新提交。'}
  const code=typeof body?.code==='string'?body.code:typeof body?.code==='number'?String(body.code):''
  const message=response.status===403?(forbidden[code]||messages[403]):(messages[response.status]||'服务异常，请稍后重试')
  throw new AuthError(response.status,message,throttle)
 }
 return body.data as T
}
export async function refreshCsrf(){csrfToken=(await call<{csrf_token:string}>('csrf')).csrf_token}
export function clearCsrf(){csrfToken=''}
export async function request<T>(action:'login'|'register',data:Record<string,string>,signal?:AbortSignal):Promise<T>{
 await refreshCsrf();return call<T>(action,'POST',data,signal)
}