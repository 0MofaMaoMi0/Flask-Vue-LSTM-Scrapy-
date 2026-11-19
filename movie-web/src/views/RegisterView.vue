<script setup lang="ts">
import {reactive,ref,onBeforeUnmount} from 'vue'
import {request} from '../api/auth'
const username=ref(''),password=ref(''),confirm=ref(''),remember=ref(false)
const visible=reactive({password:false,confirm:false}),busy=ref(false),error=ref(''),message=ref('')
const controller=new AbortController()
onBeforeUnmount(()=>{controller.abort();password.value='';confirm.value=''})
try { username.value=localStorage.getItem('movieinsight-username')||'';remember.value=!!username.value } catch {}
async function submit(){
 if(busy.value)return
 error.value='';message.value=''
 const user=username.value.normalize('NFKC').trim()
 const userLen=Array.from(user).length
 if(!user||!password.value||!confirm.value){error.value='请填写全部注册信息';return}
 if(userLen<3||userLen>32||!/^[\p{L}\p{N}_-]+$/u.test(user)){error.value='用户名需为 3–32 个字符，仅支持字母、数字、下划线或连字符';return}
 if(Array.from(password.value).length<6||Array.from(password.value).length>128){error.value='密码需要 6–128 个字符';return}
 if(password.value!==confirm.value){error.value='两次输入的密码不一致';return}
 busy.value=true
 try {
 await request('register',{username:user,password:password.value},controller.signal); password.value='';confirm.value='';message.value='注册成功，请点击下方去登录';
 }catch(e){if(!controller.signal.aborted)error.value=e instanceof Error?e.message:'请求失败，请重试'}finally{busy.value=false}
}
</script>
<template><section class="screen auth active" id="register" aria-label="注册页面">
    <div class="auth-shell">
      <aside class="story"><span class="tiny-label">创建档案</span><div><h2 class="story-title">每段旅程，<br> <em>始于一帧。</em></h2><p class="story-desc">从这里开启自己的电影档案，收藏故事，发现更多可能。</p></div><span class="story-bottom">你的第一部，从这里开始</span></aside>
      <div class="form-panel"><button class="back" @click="$router.push('/')" type="button">← 返回欢迎页</button><div class="form-kicker">会员注册</div><h2 class="form-title">创建账号</h2><p class="form-subtitle">填写基本信息，开启你的电影旅程。</p>
        <form id="registerForm" class="fields" novalidate @submit.prevent="submit" :aria-busy="busy">
          <div class="field"><label for="registerName">用户名</label><input id="registerName" v-model="username" type="text" required autocomplete="username" placeholder="设置用户名" maxlength="32"></div>
          <div class="field"><label for="registerPassword">密码</label><div class="password-wrap"><input id="registerPassword" v-model="password" :type="visible.password ? 'text' : 'password'" required autocomplete="new-password" minlength="6" maxlength="128" placeholder="6–128 个字符"><button class="showpass" type="button" @click="visible.password = !visible.password" :aria-pressed="visible.password">{{ visible.password ? '隐藏' : '显示' }}</button></div></div>
          <div class="field"><label for="confirmPassword">确认密码</label><div class="password-wrap"><input id="confirmPassword" v-model="confirm" :type="visible.confirm ? 'text' : 'password'" required autocomplete="new-password" placeholder="再次输入密码"><button class="showpass" type="button" @click="visible.confirm = !visible.confirm" :aria-pressed="visible.confirm">{{ visible.confirm ? '隐藏' : '显示' }}</button></div></div>
          <p class="form-error" :class="{show:error}" id="registerError" role="alert">{{ error }}</p><button class="submit" type="submit" :disabled="busy">创 建 账 号 →</button><p class="message" id="registerMessage" role="status">{{ message }}</p>
        </form><p class="form-hint">已经有账号？<RouterLink to="/login">去登录</RouterLink></p>
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