// One set of supported settings destinations for navigation and both AI surfaces.
export const ACCOUNT_SETTINGS = ['account','display','ai','tmdb','privacy','about'] as const
export type AccountSettingsTab = typeof ACCOUNT_SETTINGS[number]
export const OPEN_ACCOUNT_SETTINGS = 'mi2-open-account-settings'
export function isAccountSettingsTab(value:unknown):value is AccountSettingsTab {
 return typeof value==='string' && (ACCOUNT_SETTINGS as readonly string[]).includes(value)
}
export function accountSettingsTarget(tab:AccountSettingsTab){
 if(!isAccountSettingsTab(tab))throw new Error('Unsupported settings destination')
 return {path:'/ai',query:{settings:tab}}
}
