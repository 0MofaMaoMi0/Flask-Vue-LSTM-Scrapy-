<script setup lang="ts">
import {ref} from 'vue'
import {useRouter} from 'vue-router'
import {useAuthStore} from '../stores/auth'
const auth=useAuthStore(),router=useRouter(),busy=ref(false),error=ref('')
async function logout(){if(busy.value)return;busy.value=true;try{await auth.logout();await router.replace('/login')}catch{error.value='退出未确认，服务端会话可能仍有效，请重试';busy.value=false}}
</script>
<template><main class="screen welcome active"><div class="home-placeholder"><h1>登录成功</h1><p>{{ auth.user?.username }}</p><p>此页仅用于认证验证，电影首页尚未接入。</p><button class="liquid" :disabled="busy" @click="logout">退出登录</button><p role="alert">{{ error }}</p></div></main></template>
