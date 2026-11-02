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
 const user=username.value.trim()
 if(!user||!password.value||!confirm.value){error.value='请填写全部注册信息';return} if(Array.from(password.value).length<6||Array.from(password.value).length>128){error.value='密码需要 6–128 个字符';return} if(password.value!==confirm.value){error.value='两次输入的密码不一致';return}
 busy.value=true
 try {
 await request('register',{username:user,password:password.value},controller.signal); password.value='';confirm.value='';message.value='注册成功，请点击下方去登录';
 }catch(e){if(!controller.signal.aborted)error.value=e instanceof Error?e.message:'请求失败，请重试'}finally{busy.value=false}
}
</script>
<template><section class="screen auth active" id="register" aria-label="注册页面">
    <div class="auth-shell">
      <aside class="story"><span class="tiny-label">02 / YOUR FIRST FRAME</span><div><h2 class="story-title">每段旅程，<br> <em>始于一帧。</em></h2><p class="story-desc">从这里开启自己的电影档案，收藏故事，发现更多可能。</p></div><span class="story-bottom">A NEW STORY IS ABOUT TO BEGIN</span></aside>
      <div class="form-panel"><button class="back" @click="$router.push('/')" type="button">← 返回欢迎页</button><div class="form-kicker">MEMBERSHIP / SIGN UP</div><h2 class="form-title">创建账号</h2><p class="form-subtitle">填写基本信息，开启你的电影旅程。</p>
        <form id="registerForm" class="fields" novalidate @submit.prevent="submit" :aria-busy="busy">
          <div class="field"><label for="registerName">用户名</label><input id="registerName" v-model="username" type="text" required autocomplete="username" placeholder="设置用户名" maxlength="32"></div>
          <div class="field"><label for="registerPassword">密码</label><div class="password-wrap"><input id="registerPassword" v-model="password" :type="visible.password ? 'text' : 'password'" required autocomplete="new-password" minlength="6" maxlength="128" placeholder="6–128 个字符"><button class="showpass" type="button" @click="visible.password = !visible.password" :aria-pressed="visible.password">{{ visible.password ? '隐藏' : '显示' }}</button></div></div>
          <div class="field"><label for="confirmPassword">确认密码</label><div class="password-wrap"><input id="confirmPassword" v-model="confirm" :type="visible.confirm ? 'text' : 'password'" required autocomplete="new-password" placeholder="再次输入密码"><button class="showpass" type="button" @click="visible.confirm = !visible.confirm" :aria-pressed="visible.confirm">{{ visible.confirm ? '隐藏' : '显示' }}</button></div></div>
          <p class="form-error" :class="{show:error}" id="registerError" role="alert">{{ error }}</p><button class="liquid submit" type="submit" :disabled="busy">创 建 账 号 →</button><p class="message" id="registerMessage" role="status">{{ message }}</p>
        </form><p class="form-hint">已经有账号？<RouterLink to="/login">去登录</RouterLink></p>
      </div>
    </div>
  </section></template>