// One set of supported settings destinations for navigation and both AI surfaces.
export const ACCOUNT_SETTINGS = ['account','display','ai','tmdb','privacy','about'] as const
export type AccountSettingsTab = typeof ACCOUNT_SETTINGS[number]
export function isAccountSettingsTab(value:unknown):value is AccountSettingsTab {
 return typeof value==='string' && (ACCOUNT_SETTINGS as readonly string[]).includes(value)
}
export function settingsReturnTarget(value:unknown):string{
 if(typeof value!=='string'||!value.startsWith('/')||value.startsWith('//')||/[\\\r\n]/.test(value))return '/home'
 try{
  const url=new URL(value,'https://movieinsight.invalid')
  if(url.origin!=='https://movieinsight.invalid')return '/home'
  if(url.pathname==='/settings')return safeReturn(url.searchParams.get('return_to'))
  return safeReturn(value)
 }catch{return '/home'}
}
function safeReturn(value:unknown):string{
 if(typeof value!=='string'||!/^\/(?:home|ai|movies|recommendations|insights|admin)(?:[?#]|$)/.test(value)||/[\\\r\n]/.test(value))return '/home'
 const url=new URL(value,'https://movieinsight.invalid')
 if(url.pathname==='/ai'&&url.searchParams.has('settings')){
  url.searchParams.delete('settings')
  return url.pathname+url.search+url.hash
 }
 return value
}
export function accountSettingsTarget(tab:AccountSettingsTab,from?:unknown){
 if(!isAccountSettingsTab(tab))throw new Error('Unsupported settings destination')
 return {path:'/settings',query:{tab,return_to:settingsReturnTarget(from)}}
}
