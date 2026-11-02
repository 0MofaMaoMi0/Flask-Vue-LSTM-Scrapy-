<script setup lang="ts">
import {reactive,ref,onBeforeUnmount} from 'vue'
import {useRouter} from 'vue-router'
import {request} from '../api/auth'
import {useAuthStore} from '../stores/auth'
const router=useRouter(), auth=useAuthStore()
const username=ref(''),password=ref(''),confirm=ref(''),remember=ref(false)
const visible=reactive({password:false,confirm:false}),busy=ref(false),error=ref(''),message=ref('')
const controller=new AbortController()
onBeforeUnmount(()=>{controller.abort();password.value='';confirm.value=''})
try { username.value=localStorage.getItem('movieinsight-username')||'';remember.value=!!username.value } catch {}
async function submit(){
 if(busy.value)return
 error.value='';message.value=''
 const user=username.value.trim()
 if(!user||!password.value){error.value='请输入用户名和密码';return}
 busy.value=true
 try {
 await request('login',{username:user,password:password.value},controller.signal); await auth.signedIn(); password.value=''; try{if(remember.value)localStorage.setItem('movieinsight-username',user);else localStorage.removeItem('movieinsight-username')}catch{}; await router.push('/home')
 }catch(e){if(!controller.signal.aborted)error.value=e instanceof Error?e.message:'请求失败，请重试'}finally{busy.value=false}
}
</script>
<template><section class="screen auth active" id="login" aria-label="登录页面">
    <div class="auth-shell">
      <aside class="story"><span class="tiny-label">01 / THE STORY CONTINUES</span><div><h2 class="story-title">光影未止，<br> <em>故事继续。</em></h2><p class="story-desc">穿越每一帧光影，让数据为你找到下一部值得珍藏的电影。</p></div><span class="story-bottom">THE FRAME IS YOURS TO DISCOVER</span></aside>
      <div class="form-panel"><button class="back" @click="$router.push('/')" type="button">← 返回欢迎页</button><div class="form-kicker">MEMBERSHIP / SIGN IN</div><h2 class="form-title">登录账号</h2><p class="form-subtitle">继续探索，未完待续的电影世界。</p>
        <form id="loginForm" class="fields" novalidate @submit.prevent="submit" :aria-busy="busy">
          <div class="field"><label for="loginName">用户名</label><input id="loginName" v-model="username" type="text" required autocomplete="username" placeholder="请输入用户名" maxlength="32"></div>
          <div class="field"><label for="loginPassword">密码</label><div class="password-wrap"><input id="loginPassword" v-model="password" :type="visible.password ? 'text' : 'password'" required autocomplete="current-password" maxlength="128" placeholder="请输入登录密码"><button class="showpass" type="button" @click="visible.password = !visible.password" :aria-pressed="visible.password">{{ visible.password ? '隐藏' : '显示' }}</button></div></div>
          <div class="form-options"><label class="check"><input type="checkbox" id="rememberName" v-model="remember"> 记住用户名</label><span>SECURE ENTRANCE</span></div>
          <p class="form-error" :class="{show:error}" id="loginError" role="alert">{{ error }}</p>
          <button type="submit" :disabled="busy" class="liquid submit">登 录 →</button><p class="message" id="loginMessage" role="status">{{ message }}</p>
        </form><p class="form-hint">尚无账号？<RouterLink to="/register">去注册</RouterLink></p>
      </div>
    </div>
  </section></template>