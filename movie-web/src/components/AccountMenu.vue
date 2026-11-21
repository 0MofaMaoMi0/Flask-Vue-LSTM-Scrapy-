<script setup lang="ts">
// Adapted from shadcn-vue sidebar-07/NavUser (MIT; see AccountMenu.LICENSE).
// Vuetify's installed menu/list primitives replace the original Reka wrappers.
import {computed,onDeactivated,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {VMenu} from 'vuetify/components/VMenu'
import {VList,VListItem} from 'vuetify/components/VList'
import {useAuthStore} from '../stores/auth'
import {aiWorkspace} from '../services/aiWorkspace'
import {accountSettingsTarget,type AccountSettingsTab} from '../services/accountSettings'
import {siteTheme} from '../services/theme'

const props=defineProps<{collapsed:boolean}>()
const auth=useAuthStore(),route=useRoute(),router=useRouter()
const open=ref(false),signingOut=ref(false),error=ref('')
const initial=computed(()=>auth.user?.username?.slice(0,1).toUpperCase()||'')
const roleLabel=computed(()=>auth.user?.is_admin?'管理员':'个人账户')
const items:{tab:AccountSettingsTab,label:string,icon:string}[]=[
 {tab:'account',label:'个人资料',icon:'M20 21v-2a7 7 0 0 0-14 0v2M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0Z'},
 {tab:'display',label:'个性化',icon:'M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6'},
 {tab:'ai',label:'AI 与模型连接',icon:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z'},
 {tab:'tmdb',label:'TMDB 数据连接',icon:'M20 5c0 4-16 4-16 0s16-4 16 0ZM4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0'},
 {tab:'privacy',label:'隐私与账户',icon:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Zm-4 9 3 3 5-6'},
 {tab:'about',label:'关于 MovieInsight',icon:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0ZM12 11v6M12 7v.1'},
]
async function go(tab:AccountSettingsTab){
 if(signingOut.value)return
 open.value=false;error.value=''
 await router.push(accountSettingsTarget(tab,route.fullPath))
}
async function logout(){
 if(signingOut.value)return
 signingOut.value=true;error.value=''
 try{await auth.logout();open.value=false;await router.replace('/login')}
 catch{error.value='退出未确认，请检查连接后重试。';open.value=true}
 finally{signingOut.value=false}
}
watch(()=>route.fullPath,()=>{open.value=false;error.value=''})
watch(()=>props.collapsed,()=>{open.value=false})
onDeactivated(()=>{open.value=false})
</script>

<template>
 <VMenu v-model="open" location="top start" :offset="8" :close-on-content-click="false"
  :scrim="false" :transition="false" :width="264" :min-width="240" max-width="calc(100vw - 16px)">
  <template #activator="{props:activatorProps}">
   <button v-bind="activatorProps" type="button" class="account-trigger" :class="{'is-collapsed':collapsed}"
    aria-label="打开账户菜单" :disabled="signingOut" :title="collapsed?'账户菜单':undefined">
    <span class="account-avatar" aria-hidden="true"><img v-if="aiWorkspace.avatar" :src="aiWorkspace.avatar" alt=""/><span v-else>{{initial}}</span></span>
    <span v-if="!collapsed" class="account-trigger-name">{{auth.user?.username}}</span>
    <svg v-if="!collapsed" class="account-chevron" aria-hidden="true" viewBox="0 0 24 24"><path d="m8 9 4-4 4 4m-8 6 4 4 4-4"/></svg>
   </button>
  </template>
  <VList class="account-panel" :theme="siteTheme" role="menu" aria-label="账户菜单" :elevation="0" tabindex="-1">
   <div class="account-summary">
    <span class="account-avatar" aria-hidden="true"><img v-if="aiWorkspace.avatar" :src="aiWorkspace.avatar" alt=""/><span v-else>{{initial}}</span></span>
    <div class="account-summary-text"><strong>{{auth.user?.username}}</strong><span>{{roleLabel}}</span></div>
   </div>
   <div class="account-separator" role="separator"/>
   <VListItem v-for="item in items" :key="item.label" tag="button" type="button" role="menuitem" :link="true"
    class="account-menu-item" :disabled="signingOut" :ripple="false" @click="go(item.tab)">
    <template #prepend><svg aria-hidden="true" viewBox="0 0 24 24"><path :d="item.icon"/></svg></template>
    {{item.label}}
   </VListItem>
   <div class="account-separator" role="separator"/>
   <VListItem tag="button" type="button" role="menuitem" :link="true" class="account-menu-item account-signout"
    :disabled="signingOut" :ripple="false" @click="logout">
    <template #prepend><svg aria-hidden="true" viewBox="0 0 24 24"><path d="M9 4H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h4m6-15 7 7-7 7m7-7H9"/></svg></template>
    {{signingOut?'正在退出…':'退出登录'}}
   </VListItem>
   <p v-if="error" class="account-menu-error" role="alert">{{error}}</p>
  </VList>
 </VMenu>
</template>

<style scoped>
.account-trigger{width:100%;min-height:52px;display:flex;align-items:center;gap:11px;padding:8px 10px;border:0;border-radius:12px;background:transparent;color:var(--mi-text-soft);text-align:left;transition:background .16s ease}
.account-trigger:hover,.account-trigger[aria-expanded=true]{background:var(--mi-sidebar-active)}
.account-trigger:focus-visible{outline:2px solid var(--mi-accent);outline-offset:-2px}
.account-trigger:disabled{cursor:wait;opacity:.65}
.account-avatar{flex:0 0 32px;width:32px;height:32px;display:grid;place-items:center;overflow:hidden;border-radius:50%;background:var(--mi-surface-raised);color:var(--mi-accent);border:1px solid var(--mi-border);font-size:15px;font-weight:500}
.account-avatar img{width:100%;height:100%;object-fit:cover}
.account-trigger-name{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px;font-weight:550;flex:1}
.account-chevron{width:16px;height:16px;color:var(--mi-text-muted);flex:none}
.account-trigger svg,.account-panel svg{fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.account-trigger.is-collapsed{width:44px;min-height:44px;margin:auto;padding:6px;justify-content:center}
.account-panel.v-list{padding:7px;max-height:calc(100dvh - 24px);overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--mi-scroll-thumb) transparent;border:1px solid var(--mi-border);border-radius:14px;background:var(--mi-surface);color:var(--mi-text-ivory);box-shadow:var(--mi-shadow-lg);animation:account-menu-enter .16s cubic-bezier(.16,1,.3,1)}
.account-summary{display:flex;align-items:center;gap:11px;padding:10px 9px 12px}
.account-summary .account-avatar{flex-basis:36px;width:36px;height:36px}
.account-summary-text{min-width:0;display:flex;flex-direction:column;gap:3px}
.account-summary-text strong{font-size:14px;line-height:1.4;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.account-summary-text>span{font-size:12px;color:var(--mi-text-muted);line-height:1.4}
.account-separator{height:1px;background:var(--mi-border-subtle);margin:4px 5px}
.account-menu-item.v-list-item{width:100%;min-height:38px;display:flex;gap:11px;padding:8px 10px;border:0;border-radius:7px;background:transparent;color:var(--mi-text-soft);text-align:left;font-size:13px;line-height:1.5;letter-spacing:0;transition:background .12s ease,color .12s ease}
.account-menu-item :deep(.v-list-item__prepend){display:flex;flex:none}
.account-menu-item :deep(.v-list-item__content){white-space:normal;overflow-wrap:anywhere}
.account-menu-item :deep(.v-list-item__spacer){display:none}
.account-menu-item :deep(.v-list-item__overlay),.account-menu-item :deep(.v-list-item__underlay){display:none}
.account-menu-item svg{height:17px;width:17px;color:var(--mi-text-muted)}
.account-menu-item:hover,.account-menu-item:focus-visible{background:var(--mi-surface-active);color:var(--mi-text);outline:none}
.account-menu-item:focus-visible{box-shadow:inset 0 0 0 2px var(--mi-accent)}
.account-signout.v-list-item{color:var(--mi-negative)}
.account-signout svg{color:inherit}
.account-signout:hover,.account-signout:focus-visible{background:var(--mi-negative-soft);color:var(--mi-negative)}
.account-menu-item:disabled{opacity:.5;cursor:wait}
.account-menu-error{margin:7px 9px 6px;color:var(--mi-negative);font-size:12px;line-height:1.6}
@keyframes account-menu-enter{from{opacity:.4;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
@media(prefers-reduced-motion:reduce){.account-panel.v-list{animation:none}.account-trigger,.account-menu-item{transition:none}}
</style>
