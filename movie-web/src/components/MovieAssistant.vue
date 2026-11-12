<script setup lang="ts">
import {ref,onMounted,onBeforeUnmount} from 'vue'
const open=ref(false),message=ref(''),busy=ref(false),configBusy=ref(false),error=ref(''),notice=ref(''),result=ref<any>(null),conversation=ref<string|null>(null),status=ref<any>({configured:false,enabled:false}),draft=ref('')
const apiKey=ref(''),model=ref('deepseek-flash'),output=ref(2048),consent=ref(false),paidConsent=ref(false),cacheEnabled=ref(false)
const history=ref<{question:string,answer:string}[]>([])
let controller:AbortController|null=null,pending:any=null
async function request(path:string,method='GET',body?:any,signal?:AbortSignal){
 const headers:Record<string,string>={}
 if(method!=='GET'){
  const r=await fetch('/api/v1/auth/csrf',{credentials:'same-origin'});if(!r.ok)throw new Error('登录状态已失效，请重新登录。')
  headers['X-CSRF-Token']=(await r.json()).data.csrf_token;headers['Content-Type']='application/json'
 }
 return fetch('/api/v1/assistant/'+path,{method,headers,credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body),signal})
}
async function jsonRequest(path:string,method='GET',body?:any){
 const r=await request(path,method,body);const b=await r.json();if(!r.ok||b.code!=='OK')throw new Error(r.status===401?'请重新登录。':b.code==='REQUEST_ID_CONFLICT'?'此请求编号已使用，未再次调用模型。':b.code==='KEY_CHECK_FAILED'?'密钥检查失败，请检查模型、Key或服务商网络。':b.code==='INVALID_INPUT'?'配置或输入格式无效，请检查后再试。':'操作未完成：'+b.code)
 return b.data
}
async function refreshStatus(){try{status.value=await jsonRequest('status');if(status.value.model){model.value=status.value.model;cacheEnabled.value=!!status.value.cache_enabled}}catch(e){error.value=(e as Error).message}}
async function saveConfig(){
 if(configBusy.value)return
 configBusy.value=true;error.value='';notice.value=''
 try{await jsonRequest('config','POST',{provider:'deepseek',model:model.value,api_key:apiKey.value,daily_calls:null,max_output_tokens:Number(output.value),enabled:true,consent:consent.value,cache_enabled:cacheEnabled.value});await refreshStatus();notice.value='已短期连接。保存没有调用模型；接下来的智能对话将使用你的服务商账户。'}catch(e){error.value=(e as Error).message}finally{apiKey.value='';configBusy.value=false}
}
async function verifyKey(){configBusy.value=true;error.value='';try{await jsonRequest('verify','POST',{});await refreshStatus();notice.value='已完成模型列表检查，没有发送生成请求。'}catch(e){error.value=(e as Error).message;await refreshStatus()}finally{configBusy.value=false}}
async function disconnect(){configBusy.value=true;try{await jsonRequest('disconnect','POST',{});apiKey.value='';await refreshStatus();notice.value='已断开并移除短期托管密钥；在途请求将尝试取消，已发送的调用仍可能计费。'}catch(e){error.value=(e as Error).message}finally{configBusy.value=false}}
function consume(event:string,data:any){
 if(event==='draft')draft.value=data.text||''
 if(event==='result'){
  if(data.state==='DONE'){result.value=data;conversation.value=data.conversation_id||conversation.value;history.value.push({question:pending?.message||'AI 深度解释',answer:data.answer});history.value=history.value.slice(-12);message.value='';pending=null}
  else{result.value={...data,source:'INTERRUPTED',answer:data.state==='CANCELLED'?'已取消显示。已发送的模型请求仍可能计费。':'请求未完成，未自动重试可能计费的调用。'};error.value=data.error_code||data.state}
  draft.value=''
 }
}
async function send(path:string,body:any){
 if(busy.value)return
 busy.value=true;error.value='';notice.value='';draft.value='';controller=new AbortController();pending={...body,path}
 try{
  const r=await request(path,'POST',{...body,stream:true},controller.signal)
  if(!r.ok){const b=await r.json();throw new Error(b.code==='CONVERSATION_UNAVAILABLE'?'对话已失效，请开始新对话。':b.code==='REQUEST_ID_CONFLICT'?'请求编号冲突，未重新计费。':'请求未完成：'+b.code)}
  if(!r.body)throw new Error('浏览器无法读取流式响应。')
  const reader=r.body.getReader(),decoder=new TextDecoder();let buffer=''
  while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value||new Uint8Array(),{stream:!done});let boundary:number
   while((boundary=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,boundary);buffer=buffer.slice(boundary+2);const event=frame.match(/^event: (.+)$/m)?.[1],data=frame.match(/^data: (.+)$/m)?.[1];if(event&&data)consume(event,JSON.parse(data))}
   if(done)break
  }
 }catch(e){if((e as Error).name!=='AbortError')error.value='连接中断，未自动重发。可重试原请求以读取结果，不会静默再次调用。'}finally{controller=null;busy.value=false;await refreshStatus()}
}
async function submit(){
 if(!message.value.trim()||busy.value)return
 const text=message.value.trim();const same=pending?.path==='query'&&pending.message===text&&pending.conversation_id===conversation.value
 await send('query',{message:text,conversation_id:conversation.value,request_id:same?pending.request_id:crypto.randomUUID()})
}
async function explain(id:number){await send('explain',{movie_id:id,request_id:crypto.randomUUID()})}
async function testConnection(){if(!paidConsent.value||busy.value)return;await send('test',{confirm_paid:true,request_id:crypto.randomUUID()})}
async function cancel(){
 if(!pending)return
 try{const id=pending.request_id;const data=await jsonRequest('cancel','POST',{request_id:id});if(!data.cancel_requested&&data.state==='DONE'){const completed=await jsonRequest('requests/'+id);consume('result',{...completed,state:'DONE'});notice.value='请求已经完成，未执行取消。'}else{result.value={source:'INTERRUPTED',answer:'已请求取消。已发送的调用可能计费，最终用量可能暂时未知。',cost:data.cost};notice.value='不会自动重发此请求。'}}catch(e){error.value=(e as Error).message}finally{controller?.abort();draft.value=''}
}
function reset(){if(busy.value)return;conversation.value=null;result.value=null;history.value=[];pending=null;error.value=''}
onMounted(()=>{if(new URLSearchParams(location.search).get('assistant')==='1')open.value=true;refreshStatus()})
onBeforeUnmount(()=>{apiKey.value='';controller?.abort()})
</script>
<template>
<section class="assistant" aria-label="电影助手">
<button :aria-expanded="open" aria-controls="movie-assistant-panel" @click="open=!open">{{open?'收起电影助手':'打开电影助手'}}</button>
<div v-if="open" id="movie-assistant-panel" class="assistant-panel">
<h2>电影助手</h2><p>理解你的观影想法，结合电影库与个人偏好，比较候选并解释推荐。</p>
<p class="assistant-note" role="status">{{status.cloud_available?'已启用你的 '+status.model+' · 提交对话将调用模型，由你的服务商账户计费':'未连接个人模型 · 当前使用本地兜底'}}<br>密钥仅在当前登录会话中短期托管，30分钟到期或服务重启后需重新连接。</p>
<details class="model-settings"><summary>个人模型连接与费用设置</summary>
<form @submit.prevent="saveConfig" autocomplete="off">
<label for="byok-model">DeepSeek 模型</label><select id="byok-model" v-model="model" :disabled="configBusy||busy"><option value="deepseek-flash">DeepSeek Flash</option><option value="deepseek-v4-pro">DeepSeek V4 Pro（通常费用更高）</option></select>
<label for="byok-key">API Key（不会回显或保存到浏览器）</label><input id="byok-key" v-model="apiKey" type="password" autocomplete="off" spellcheck="false" autocapitalize="off" maxlength="512" required :disabled="configBusy||busy"/>
<div class="settings-grid"><p class="assistant-note">每日及每轮模型调用次数：不限（按你的设置）</p><label>每次最大输出 Token<input v-model.number="output" type="number" min="256" max="8192" required/></label></div>
<label class="check"><input v-model="consent" type="checkbox" required/>我同意向所选服务商发送对话和必要的电影/偏好信息；启用后提交智能请求可能产生费用。</label>
<label class="check"><input v-model="cacheEnabled" type="checkbox"/>允许5分钟内复用相同用户、上下文和电影/偏好版本的回答（默认关闭）</label><button type="submit" :disabled="configBusy||busy||!consent||!apiKey">保存并启用个人模型</button>
</form>
<div class="assistant-controls"><button type="button" :disabled="configBusy||busy||!status.configured" @click="verifyKey">检查 Key 与模型列表</button><button type="button" :disabled="configBusy||!status.configured" @click="disconnect">断开连接并移除 Key</button></div>
<p class="assistant-note">状态：{{status.verification||'DISCONNECTED'}}；今日已派发 {{status.daily_calls_used??0}} / {{status.daily_calls??'不限'}} 次（UTC日）。列表检查不发送生成请求。不设每日或每轮调用次数上限；仍受90秒超时、并发和上下文长度保护。金额未取得可靠账单时显示未知。</p>
<label class="check"><input v-model="paidConsent" type="checkbox"/>我明确同意发送一次可能计费的模型连接测试。</label><button type="button" :disabled="busy||configBusy||!paidConsent||!status.enabled" @click="testConnection">发送模型测试（可能计费）</button>
</details>
<div v-if="history.length>1" class="history"><details><summary>本次对话记录</summary><div v-for="(turn,i) in history.slice(0,-1)" :key="i"><p>你：{{turn.question}}</p><p class="answer">助手：{{turn.answer}}</p></div></details></div>
<form @submit.prevent="submit"><label for="assistant-message">你想看什么？</label><textarea id="assistant-message" v-model="message" maxlength="2000" rows="3" placeholder="例如：根据我的收藏推荐两部电影，比较它们；也可以探索库外电影" required :disabled="busy"/><div class="assistant-controls"><button type="submit" :disabled="busy||!message.trim()">{{busy?'正在处理…':status.cloud_available?'发送给 AI 电影助手':'查找电影'}}</button><button v-if="busy" type="button" @click="cancel">取消本次请求</button><button type="button" :disabled="busy" @click="reset">开始新对话</button></div></form>
<p v-if="error" role="alert">{{error}}</p><p v-if="notice" role="status">{{notice}}</p>
<div v-if="busy" aria-live="polite"><p>正在理解与查询；下方生成草稿尚未完成事实核验。</p><p class="answer">{{draft}}</p></div>
<div v-if="result" aria-live="polite"><p>{{result.source==='LLM'?'AI 生成回答':result.source==='CACHE'?'AI 缓存回答（未再次调用）':result.source==='MOCK'?'模拟模型测试':result.source==='FALLBACK'?'AI 不可用，已降级':result.source==='INTERRUPTED'?'请求已中断':'本地结果'}}</p><p class="answer">{{result.answer}}</p>
<p v-if="result.cost" class="assistant-note">模型调用 {{result.cost.provider_calls??0}} 次 · 输入 {{result.cost.input_tokens??'未知'}} / 输出 {{result.cost.output_tokens??'未知'}} Token · 金额 {{result.cost.amount===0?'0':'未知'}}<span v-if="result.replayed"> · 已读取原请求结果，未再次调用模型</span></p>
<ul><li v-for="movie in result.items" :key="movie.movie_id"><a :href="'#movie/'+movie.movie_id">{{movie.title}}</a><span> {{movie.release_year||'年份未知'}} · {{movie.genres?.join(' / ')}} · 库内已核验</span><p>{{movie.reasons?.join('；')}}</p><button type="button" :disabled="busy" @click="explain(movie.movie_id)">{{status.cloud_available?'让 AI 解释这部电影':'查看本地推荐依据'}}</button></li></ul>
<div v-if="result.discoveries?.length"><h3>库外电影发现</h3><p class="assistant-note">以下影片没有入库，不提供库内详情或行为操作。</p><ul><li v-for="movie in result.discoveries" :key="movie.title"><strong>{{movie.title}}</strong> · {{movie.year||'年份未确认'}}<p>{{movie.reason}}</p><p class="assistant-note">{{movie.verification==='TMDB_TITLE_YEAR_MATCH'?'TMDB标题与年份匹配；仍未入库':'未经 TMDB 核实的模型建议'}}</p><a v-if="movie.tmdb_url" :href="movie.tmdb_url" target="_blank" rel="noopener noreferrer">查看 TMDB 资料</a></li></ul></div>
<p v-for="limit in result.limitations" :key="limit" class="assistant-note">{{limit}}</p></div>
</div></section>
</template>
<style scoped>
.assistant{margin:24px 0;border-block:1px solid #49545d;padding:20px 0;color:#f1ede5}.assistant-panel{max-width:75ch;padding-top:20px}.assistant h2{font-size:24px;margin:0 0 16px}.assistant p{line-height:1.7}.assistant-note{color:#c0c8cd;font-size:13px}.assistant label{display:block;margin:20px 0 8px}.assistant :is(textarea,input:not([type=checkbox]),select){box-sizing:border-box;width:100%;background:#20272d;color:#f1ede5;border:1px solid #65717b;border-radius:4px;padding:12px;font:inherit;min-width:0}.assistant textarea{resize:vertical}.assistant textarea::placeholder{color:#bdc5cc}.assistant-controls{display:flex;gap:12px;flex-wrap:wrap;margin-top:12px}.assistant button{min-height:44px;border:0;border-radius:4px;padding:11px 16px;background:#dfba7f;color:#241d13;font:inherit;cursor:pointer}.assistant button:disabled{opacity:.5;cursor:default}.assistant :is(button,textarea,input,select,summary,a):focus-visible{outline:2px solid #f0cf98;outline-offset:4px}.assistant a{color:#f0cf98;text-underline-offset:4px}.assistant li{padding:12px 0}.assistant li span{font-size:13px;color:#c0c8cd}.model-settings{border-block:1px solid #49545d;padding:18px 0;margin:20px 0}.model-settings summary,.history summary{cursor:pointer}.assistant .check{display:flex;flex-direction:row;align-items:flex-start;gap:10px;font-size:13px;line-height:1.7}.assistant .check input{width:18px;height:18px;padding:0;min-width:18px;margin:3px 0 0;flex:none;accent-color:#dfba7f}.settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.answer{white-space:pre-wrap;overflow-wrap:anywhere}.assistant :is(input,textarea){caret-color:#f0cf98}.assistant ::selection{background:#dfba7f;color:#241d13}.history{margin:18px 0}@media(max-width:500px){.settings-grid{grid-template-columns:1fr}.assistant button{max-width:100%;overflow-wrap:anywhere}}
</style>
