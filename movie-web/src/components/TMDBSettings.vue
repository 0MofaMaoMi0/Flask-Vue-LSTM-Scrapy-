<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount} from 'vue'
import {tmdbRequest,tmdbChanged,tmdbErrors} from '../services/tmdb'
const props=defineProps<{idPrefix:string}>()
const token=ref(''),consent=ref(false),busy=ref(false),error=ref(''),notice=ref(''),status=ref<any>({configured:false}),results=ref<any[]>([])
const label:Record<string,string>={DISCONNECTED:'未连接 · 使用缓存',UNVERIFIED:'已启用 · 待检测',VERIFIED:'连接正常',KEY_REJECTED:'令牌不可用'}
async function refreshStatus(){try{status.value=await tmdbRequest('status')}catch(e){error.value=(e as Error).message}}
async function action(kind:string){
 if(busy.value)return;busy.value=true;error.value='';notice.value='';results.value=[]
 const value=token.value;token.value=''
 try{
  if(kind==='config'){await tmdbRequest('config','POST',{access_token:value,consent:consent.value});notice.value='令牌已在服务端短期托管。可点击检测连接，或打开推荐频道按需获取数据。'}
  if(kind==='verify'){await tmdbRequest('verify','POST',{});notice.value='TMDB 连接检测成功；没有发送模型生成请求。'}
  if(kind==='refresh'){const r=await tmdbRequest('refresh','POST',{});results.value=r.results;notice.value=r.all_updated?'热门、今日与本周热榜已更新（短时间内复用最新缓存）。':'部分榜单未更新；已有缓存仍可使用。'}
  if(kind==='disconnect'){await tmdbRequest('disconnect','POST',{});notice.value='已移除当前登录会话托管的令牌；电影公开缓存保留。'}
  await refreshStatus();tmdbChanged()
 }catch(e){error.value=(e as Error).message;await refreshStatus();tmdbChanged()}finally{busy.value=false}
}
function date(s?:string){return s?new Date(s).toLocaleString('zh-CN',{hour12:false}):'尚无'}
onMounted(()=>{refreshStatus();window.addEventListener('mi2-tmdb-status',refreshStatus);window.addEventListener('focus',refreshStatus)})
onBeforeUnmount(()=>{token.value='';window.removeEventListener('mi2-tmdb-status',refreshStatus);window.removeEventListener('focus',refreshStatus)})
</script>
<template><div class="tmdb-settings">
 <h2>TMDB 数据连接</h2><p>为电影推荐、热榜与 AI 工具提供按需在线资料。无需同时填写 API Key，也不需要配置 AI 模型。</p>
 <form @submit.prevent="action('config')" autocomplete="off">
  <label :for="idPrefix+'-tmdb-token'">API Read Access Token</label><input :id="idPrefix+'-tmdb-token'" v-model="token" type="password" maxlength="2048" autocomplete="off" autocapitalize="off" spellcheck="false" :disabled="busy" required placeholder="在 TMDB 账户的 API 设置中获取"/>
  <p><a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noopener noreferrer">前往 TMDB 获取令牌（新标签页）</a>。令牌不会回显，不保存到浏览器或明文数据库。</p>
  <label class="consent"><input v-model="consent" type="checkbox" :disabled="busy" required/>我允许服务器使用此令牌访问 TMDB 官方接口，按需获取公开电影资料；不向 TMDB 发送聊天、个人评分或收藏记录。</label>
  <button class="primary" type="submit" :disabled="busy||!token||!consent">保存并启用 TMDB</button>
 </form>
 <div class="connection-state" role="status"><strong>{{status.last_error?'最近访问未成功 · 保留缓存':label[status.verification]||'读取连接状态'}}</strong><span v-if="status.configured">当前会话剩余约 {{Math.ceil(status.expires_in_seconds/60)}} 分钟</span><span>最后成功访问：{{date(status.last_success_at)}}</span><span v-if="status.last_error">{{tmdbErrors['TMDB_'+status.last_error]||'最近访问未成功，可重新检测连接。'}}</span></div>
 <div class="actions"><button :disabled="busy||!status.configured" @click="action('verify')">检测连接</button><button :disabled="busy||!status.configured" @click="action('refresh')">立即更新三个热榜</button><button :disabled="busy||!status.configured" @click="action('disconnect')">断开并移除令牌</button><button :disabled="busy" @click="refreshStatus">读取状态</button></div>
 <p v-if="busy" role="status">正在处理 TMDB 请求，请稍候…</p><p v-if="error" class="error" role="alert">{{error}}</p><p v-if="notice" class="notice" role="status">{{notice}}</p>
 <ul v-if="results.length"><li v-for="r in results" :key="r.channel">{{({popular:'热门推荐',trending_day:'今日热榜',trending_week:'本周热榜'} as Record<string,string>)[r.channel]}}：{{r.status==='OK'?'已取得可用资料':tmdbErrors['TMDB_'+r.status]||'暂未更新'}}<span v-if="r.captured_at"> · {{date(r.captured_at)}}</span></li></ul>
 <p class="help">榜单按需刷新，缓存约 {{status.board_cache_seconds/60||15}} 分钟；影片详情缓存约 {{status.detail_cache_seconds/3600||24}} 小时。连接失效时继续使用可用缓存。仅更新读取资料，不会自动新增或改写正式电影库。退出登录、过期或服务重启后需重新连接。</p>
</div></template>
<style scoped>
.tmdb-settings{color:#eee8f7}.tmdb-settings h2{font-size:20px;margin:0 0 12px}.tmdb-settings p,.tmdb-settings li{color:#c1b4ce;font-size:13px;line-height:1.85}.tmdb-settings a{color:#d3b5f6;text-underline-offset:4px}.tmdb-settings label{display:block;font-size:14px;margin:18px 0 9px}.tmdb-settings input[type=password]{display:block;width:100%;max-width:600px;background:#0c0b12;border:1px solid #665672;border-radius:7px;padding:12px;color:#f5ecff;font:inherit;caret-color:#d3b5f6}.tmdb-settings input::placeholder{color:#b8a6c7}.tmdb-settings .consent{display:flex;align-items:flex-start;gap:9px;font-size:13px;line-height:1.8}.consent input{accent-color:#c2a1e5;margin-top:5px;flex:none}.tmdb-settings button{font:inherit;font-size:13px;color:#e9def7;border:1px solid #695480;background:#21192e;border-radius:6px;padding:10px 14px;cursor:pointer}.tmdb-settings button:hover{background:#30223f}.tmdb-settings button.primary{background:#c2a1e5;color:#170f24;border-color:#c2a1e5;font-weight:700}.tmdb-settings button:disabled{opacity:.45;cursor:not-allowed}.tmdb-settings :focus-visible{outline:2px solid #d6b8fa;outline-offset:3px}.connection-state{display:flex;flex-direction:column;gap:8px;margin:25px 0 18px;font-size:13px;color:#c7b6d8}.connection-state strong{color:#ede1fa;font-size:15px}.actions{display:flex;flex-wrap:wrap;gap:10px}.tmdb-settings .error{color:#f3b4c4}.tmdb-settings .notice{color:#c3dfd4}.tmdb-settings .help{margin-top:23px}.tmdb-settings ul{padding-left:20px}
</style>
