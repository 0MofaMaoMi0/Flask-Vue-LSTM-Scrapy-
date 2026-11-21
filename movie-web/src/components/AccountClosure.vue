<script setup lang="ts">
import {ref,onBeforeUnmount} from 'vue'
import {useAuthStore} from '../stores/auth'
const props=defineProps<{idPrefix:string}>(),auth=useAuthStore()
const open=ref(false),password=ref(''),confirmation=ref(''),understand=ref(false),busy=ref(false),error=ref('')
onBeforeUnmount(()=>{password.value='';confirmation.value=''})
async function closeAccount(){
 if(busy.value||!understand.value||confirmation.value!==auth.user?.username)return
 busy.value=true;error.value='';const value=password.value;password.value=''
 try{
  const c=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});if(!c.ok)throw new Error('请先重新登录。')
  const token=(await c.json()).data.csrf_token
  const r=await fetch('/api/v1/account/close',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':token},body:JSON.stringify({password:value,confirmation:confirmation.value,understand:true})});const b=await r.json()
  if(!r.ok)throw new Error(b.code==='CURRENT_PASSWORD_REJECTED'?'当前密码不正确，账户未注销。':b.code==='ACCOUNT_CONFIRMATION_REQUIRED'?'请输入完整账户名称。':b.code==='RATE_LIMITED'||r.status===429?'尝试过于频繁，请稍后重试。':'未取得注销确认，请核对账户状态后再操作。')
  window.location.replace('/login?account=closed')
 }catch(e){error.value=(e as Error).message||'未取得注销确认，请尝试登录核对状态，不自动重复提交。'}finally{busy.value=false}
}
</script>
<template><section class="closure" aria-label="注销账户"><h2>注销账户</h2><p>永久停用此账户、退出全部设备，并删除在线数据库中保存的全部 API 密钥。此操作不能在页面内撤销；同名账户不再开放注册。</p><p>电影目录不会删除。已有观影和审计等关联历史保留，历史备份按原保留规则处理。</p><button v-if="!open" @click="open=true">了解并准备注销</button><form v-else @submit.prevent="closeAccount" autocomplete="off"><label :for="idPrefix+'-close-password'">当前登录密码</label><input :id="idPrefix+'-close-password'" type="password" v-model="password" autocomplete="off" maxlength="512" required :disabled="busy"/><label :for="idPrefix+'-close-name'">输入账户名称 {{auth.user?.username}} 确认</label><input :id="idPrefix+'-close-name'" v-model="confirmation" autocomplete="off" required :disabled="busy"/><label class="check"><input v-model="understand" type="checkbox" required :disabled="busy"/>我理解账户将永久停用，在线保存的密钥会被删除。</label><div><button type="submit" class="danger" :disabled="busy||!password||!understand||confirmation!==auth.user?.username">永久注销账户并删除密钥</button><button type="button" :disabled="busy" @click="open=false;password='';confirmation='';understand=false;error=''">取消</button></div></form><p v-if="error" role="alert" class="error">{{error}}</p></section></template>
<style scoped>
.closure{background:var(--mi-surface);border:1px solid var(--mi-border);border-radius:14px;padding:24px;margin-top:22px;color:var(--mi-text-ivory);box-shadow:var(--mi-shadow-sm)}.closure h2{font-size:18px;margin:0 0 12px;color:var(--mi-text)}.closure p{font-size:13px;line-height:1.8;color:var(--mi-text-secondary)}.closure label{display:block;font-size:13px;margin:15px 0 7px;color:var(--mi-text-soft)}.closure input:not([type=checkbox]){display:block;width:100%;max-width:420px;background:var(--mi-input-bg);border:1px solid var(--mi-border-strong);padding:10px;border-radius:8px;color:var(--mi-text);font:inherit}.closure input:not([type=checkbox]):focus{border-color:var(--mi-accent);outline:none}.closure .check{display:flex;align-items:center;gap:9px;line-height:1.8;color:var(--mi-text-soft)}.closure input[type=checkbox]{accent-color:var(--mi-accent)}.closure button{font:inherit;font-size:13px;background:transparent;color:var(--mi-text-soft);border:1px solid var(--mi-border-strong);padding:10px 14px;border-radius:8px;cursor:pointer;margin:6px 10px 0 0;transition:background-color .18s ease,border-color .18s ease}.closure button:hover:not(:disabled){background:var(--mi-surface-hover);border-color:var(--mi-border-strong);color:var(--mi-text)}.closure button.danger{background:var(--mi-negative-soft);color:var(--mi-negative);border-color:var(--mi-negative-border)}.closure button.danger:hover:not(:disabled){background:var(--mi-negative-soft);border-color:var(--mi-negative);color:var(--mi-negative)}.closure button:disabled{opacity:.45;cursor:not-allowed}.closure :focus-visible{outline:2px solid var(--mi-accent);outline-offset:3px}.closure .error{color:var(--mi-negative)}
</style>
