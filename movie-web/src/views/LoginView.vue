<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,reactive,ref} from 'vue'
import {useRouter} from 'vue-router'
import {AuthError,getAuthThrottle,request} from '../api/auth'
import {useAuthStore} from '../stores/auth'
const router=useRouter(), auth=useAuthStore()
const username=ref(''),password=ref(''),remember=ref(false)
const visible=reactive({password:false,confirm:false}),busy=ref(false),error=ref(''),message=ref('')
const cooldownUntil=ref(0),now=ref(Date.now())
const isCooling=computed(()=>cooldownUntil.value>now.value)
const waitSeconds=computed(()=>Math.max(1,Math.ceil((cooldownUntil.value-now.value)/1000)))
const cooldownHint=computed(()=>isCooling.value?`操作过于频繁，请 ${waitSeconds.value} 秒后重试。`:'')
let cooldownTimer:ReturnType<typeof setInterval>|undefined
const controller=new AbortController()
function clearCooldownTimer(){if(cooldownTimer!==undefined){clearInterval(cooldownTimer);cooldownTimer=undefined}}
function startCooldown(until:number){
 if(!(until>Date.now()))return
 cooldownUntil.value=until;now.value=Date.now();error.value=''
 clearCooldownTimer()
 cooldownTimer=setInterval(()=>{
  now.value=Date.now()
  if(now.value>=cooldownUntil.value){cooldownUntil.value=0;clearCooldownTimer()}
 },250)
}
onMounted(()=>{
 const throttle=getAuthThrottle()
 if(!throttle)return
 const until=throttle.retryAfterAt??(throttle.retryAfterSeconds!==undefined?Date.now()+throttle.retryAfterSeconds*1000:0)
 if(until>Date.now())startCooldown(until)
 else error.value='操作过于频繁，请稍后手动重试。'
})
onBeforeUnmount(()=>{controller.abort();clearCooldownTimer();password.value=''})
try { username.value=localStorage.getItem('movieinsight-username')||'';remember.value=!!username.value } catch {}
async function submit(){
 if(busy.value||isCooling.value)return
 error.value='';message.value=''
 const user=username.value.trim()
 if(!user||!password.value){error.value='请输入用户名和密码';return}
 busy.value=true
 try {
 await request('login',{username:user,password:password.value},controller.signal); await auth.signedIn(); password.value=''; try{if(remember.value)localStorage.setItem('movieinsight-username',user);else localStorage.removeItem('movieinsight-username')}catch{}; await router.push('/home')
 }catch(e){
  if(controller.signal.aborted)return
  if(e instanceof AuthError&&e.status===429){
   const until=e.retryAfterAt??(e.retryAfterSeconds!==undefined?Date.now()+e.retryAfterSeconds*1000:0)
   if(until>Date.now())startCooldown(until)
   else error.value='操作过于频繁，请稍后手动重试。'
  }else error.value=e instanceof Error?e.message:'请求失败，请重试'
 }finally{busy.value=false}
}
</script>
<template><section class="screen auth active" id="login" aria-label="登录页面">
    <div class="auth-shell">
      <aside class="story"><span class="tiny-label">会员入口</span><div><h2 class="story-title">光影未止，<br> <em>故事继续。</em></h2><p class="story-desc">穿越每一帧光影，让数据为你找到下一部值得珍藏的电影。</p></div><span class="story-bottom">从一帧开始，找到下一部</span></aside>
      <div class="form-panel"><button class="back" @click="$router.push('/')" type="button">← 返回欢迎页</button><div class="form-kicker">会员登录</div><h2 class="form-title">登录账号</h2><p class="form-subtitle">继续探索，未完待续的电影世界。</p>
        <form id="loginForm" class="fields" novalidate @submit.prevent="submit" :aria-busy="busy">
          <div class="field"><label for="loginName">用户名</label><input id="loginName" v-model="username" type="text" required autocomplete="username" placeholder="请输入用户名" maxlength="32"></div>
          <div class="field"><label for="loginPassword">密码</label><div class="password-wrap"><input id="loginPassword" v-model="password" :type="visible.password ? 'text' : 'password'" required autocomplete="current-password" maxlength="128" placeholder="请输入登录密码"><button class="showpass" type="button" @click="visible.password = !visible.password" :aria-pressed="visible.password">{{ visible.password ? '隐藏' : '显示' }}</button></div></div>
          <div class="form-options"><label class="check"><input type="checkbox" id="rememberName" v-model="remember"> 记住用户名</label><span>加密连接</span></div>
          <p class="form-error" :class="{show:error}" id="loginError" role="alert">{{ error }}</p>
          <button type="submit" :disabled="busy || isCooling" class="submit">{{ isCooling ? waitSeconds + ' 秒后可重试' : '登 录 →' }}</button><p class="message" id="loginMessage" role="status">{{ cooldownHint || message }}</p>
        </form><p class="form-hint">尚无账号？<RouterLink to="/register">去注册</RouterLink></p>
      </div>
    </div>
  </section></template>
<style scoped>
.screen.auth{padding:88px clamp(18px,4.6vw,80px)}
.auth-shell{width:min(1040px,100%);min-height:clamp(560px,74vh,720px);grid-template-columns:1.04fr .96fr;border:1px solid rgba(241,236,226,.16);border-radius:16px;background:#141519;box-shadow:0 28px 64px rgba(0,0,0,.36);backdrop-filter:none;-webkit-backdrop-filter:none;overflow:hidden}
.story{padding:clamp(30px,3.6vw,48px);background:#101114;border-right:1px solid rgba(241,236,226,.08)}
.story::before{display:none}
.tiny-label{font-size:11px;letter-spacing:.04em;color:#b7b1a5;font-weight:500}
.story-title{font-family:var(--serif);font-size:clamp(34px,3.4vw,46px);letter-spacing:.01em;font-weight:500;line-height:1.35;margin:0 0 16px;color:#fff}
.story-title em{color:#c8a46a;font-style:normal}
.story-desc{color:#b7b1a5;font-size:13px;line-height:1.9;letter-spacing:0;max-width:330px;margin:0}
.story-bottom{border-top:1px solid rgba(241,236,226,.14);padding-top:16px;color:#a9a294;letter-spacing:.02em;font-size:11px}
.form-panel{padding:64px clamp(30px,4.4vw,54px) 46px;background:#141519;justify-content:center}
.form-panel:before{display:none}
.back{position:absolute;left:clamp(30px,4.4vw,54px);top:28px;color:#a9a294;font-size:12px;letter-spacing:.02em;border-radius:6px}
.back:hover,.back:focus-visible{color:#fff}
.form-kicker{font-size:11px;letter-spacing:.04em;color:#c8a46a;margin-bottom:14px}
.form-title{font-family:var(--serif);font-size:clamp(28px,3vw,36px);letter-spacing:.01em;font-weight:500;margin:0 0 10px;color:#fff}
.form-subtitle{font-size:12.5px;line-height:1.8;color:#b7b1a5;letter-spacing:0;margin:0 0 32px}
.fields{gap:16px}
.field{gap:7px}
.field label{font-size:12px;letter-spacing:.02em;color:#d8d2c6}
.field input{height:46px;color:#fff;background:#0f1013;border:1px solid rgba(241,236,226,.18);border-radius:8px;padding:0 14px;transition:border-color .18s ease,background-color .18s ease}
.field input::placeholder{color:#7f7a70}
.field input:focus{background:#121317;border-color:#c8a46a;outline:2px solid rgba(200,164,106,.28);outline-offset:1px}
.password-wrap input{padding-right:64px}
.showpass{right:5px;top:5px;height:36px;color:#a9a294;border-radius:6px}
.form-options{font-size:12px;color:#a9a294;margin:0}
.check input{accent-color:#c8a46a}
button.submit{height:48px;border-radius:8px;background:#c8a46a;border:1px solid #c8a46a;color:#171410;font-size:13px;font-weight:500;letter-spacing:.12em;box-shadow:none;transition:background-color .18s ease,border-color .18s ease,transform .18s ease}
button.submit:hover,button.submit:focus-visible{background:#d6b57e;border-color:#d6b57e;color:#171410;transform:translateY(-1px);box-shadow:none}
button.submit:active{transform:translateY(0)}
button.submit:disabled{opacity:.55;cursor:not-allowed;transform:none}
.form-hint{margin-top:20px;color:#a9a294;font-size:12px;letter-spacing:.01em;text-align:center}
.form-hint a{color:#d8d2c6;text-underline-offset:3px}
.form-hint a:hover{color:#fff}
.form-error{color:#e8a094;font-size:12px}
.message{font-size:12px;color:#c8a46a}
.screen.auth :focus-visible{outline-color:#c8a46a}
@media(max-width:980px){.auth-shell{grid-template-columns:1fr 1fr;min-height:600px}.story-title{font-size:34px}}
@media(max-width:720px){.screen.auth{padding:72px 16px 40px}.auth-shell{display:flex;flex-direction:column;min-height:calc(100svh - 112px)}.story{min-height:150px;padding:22px 24px;border-right:0;border-bottom:1px solid rgba(241,236,226,.08)}.story-title{font-size:26px;line-height:1.3}.form-panel{flex:1;padding:48px 24px 26px}.fields{gap:12px}.form-subtitle{margin-bottom:20px}.form-hint{margin-top:16px}.back{top:20px;left:20px}.form-title{font-size:27px}}
@media(max-width:400px){.form-panel{padding-left:20px;padding-right:20px}.story{padding:20px}}
</style>