export const tmdbErrors:Record<string,string>={TMDB_KEY_REJECTED:'令牌无效或没有访问权限，请重新填写。',TMDB_NETWORK_UNAVAILABLE:'服务器暂时无法连接 TMDB，已保留缓存。',TMDB_UPSTREAM_UNAVAILABLE:'TMDB 服务暂时不可用，已保留缓存。',TMDB_DISABLED:'连接已停用，可在设置中启用已保存的令牌。',CREDENTIALS_UNAVAILABLE:'加密凭据服务暂不可用，已保存内容没有被覆盖。',TMDB_NOT_CONNECTED:'请先保存并启用 TMDB 令牌。',TMDB_DISCONNECTED:'连接状态已变化，请重新读取状态；已保存的令牌不会因此删除。',TMDB_RATE_LIMITED:'TMDB 请求受限，请等待后再试。',TMDB_COOLDOWN:'正在等待上游恢复，请稍后再试。',TMDB_CHECK_THROTTLED:'刚刚检测过连接，请稍后再试。',TMDB_INVALID_UPSTREAM:'TMDB 返回资料不完整，未替换已有缓存。',TMDB_CACHE_UNAVAILABLE:'资料缓存暂时无法保存，请稍后再试。',INVALID_INPUT:'输入无效；请使用 API Read Access Token，不是 API Key 或网址。',AUTHENTICATION_REQUIRED:'登录已失效，请重新登录。',REQUEST_VERIFICATION_FAILED:'安全验证已失效，请刷新页面后重试。'}
export async function tmdbRequest(path:string,method='GET',body?:unknown){
 const headers:Record<string,string>={},controller=new AbortController(),timer=setTimeout(()=>controller.abort(),75000)
 try{
  if(method!=='GET'){const c=await fetch('/api/v1/auth/csrf',{credentials:'same-origin',signal:controller.signal});if(!c.ok)throw new Error('登录已失效，请重新登录。');headers['X-CSRF-Token']=(await c.json()).data.csrf_token;headers['Content-Type']='application/json'}
  const r=await fetch('/api/v1/tmdb/'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});const b=await r.json()
  if(!r.ok)throw new Error((tmdbErrors[b.code]||'TMDB 操作未完成，请稍后重试。')+(b.data?.retry_after?' 请等待约 '+b.data.retry_after+' 秒。':''))
  return b.data
 }catch(e){if((e as Error).name==='AbortError')throw new Error('请求超时，未自动重试；可以重新读取状态，已有缓存保持可用。');throw e}finally{clearTimeout(timer)}
}
export function tmdbChanged(){window.dispatchEvent(new Event('mi2-tmdb-status'))}
