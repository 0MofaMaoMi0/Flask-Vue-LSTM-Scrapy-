<script setup lang="ts">
import {computed,nextTick,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import {useAuthStore} from '../stores/auth'
import TMDBSettings from './TMDBSettings.vue'
import AccountClosure from './AccountClosure.vue'
import {aiWorkspace} from '../services/aiWorkspace'
import {isAccountSettingsTab,settingsReturnTarget,type AccountSettingsTab} from '../services/accountSettings'
import {clearAssistantDisplay,type AssistantSurface} from '../services/assistantDisplay'
import {assistantJson as jsonRequest,diagnosticRequestAfterError} from '../services/assistantApi'
import {persistAvatar} from '../services/personalFeatures'
const auth=useAuthStore(),route=useRoute(),router=useRouter(),rootElement=ref<HTMLElement>()
const idPrefix='settings',uid=(id:string)=>idPrefix+'-'+id
const settingsTab=computed(()=>isAccountSettingsTab(route.query.tab)?route.query.tab:'account')
const returnTo=computed(()=>settingsReturnTarget(route.query.return_to))
const dialogMode=computed({get:()=>aiWorkspace.dialogMode,set:(value:string)=>{aiWorkspace.dialogMode=value}})
const error=ref(''),notice=ref(''),busy=ref(false),configBusy=ref(false),status=ref<any>({configured:false,enabled:false})
const apiKey=ref(''),model=ref('deepseek-flash'),output=ref(2048),consent=ref(false),paidConsent=ref(false),cacheEnabled=ref(false)
const testId=ref<string|null>(null),testResult=ref<any>(null)
let testController:AbortController|null=null
function broadcast(){window.dispatchEvent(new Event('mi2-model-status'))}
async function selectTab(tab:AccountSettingsTab){await router.replace({path:'/settings',query:{...route.query,tab}})}
function clearDisplay(surface:AssistantSurface){notice.value=clearAssistantDisplay(surface)?'已清空所选窗口的显示记录，服务端数据保留。':'该窗口正在生成，请完成或取消生成后再清空。'}
async function refreshStatus(){try{status.value=await jsonRequest('status');if(status.value.model){model.value=status.value.model;cacheEnabled.value=!!status.value.cache_enabled}}catch(e){error.value=(e as Error).message}}
async function saveConfig(){
 if(configBusy.value)return
 configBusy.value=true;error.value='';notice.value=''
 try{await jsonRequest('config','POST',{provider:'deepseek',model:model.value,api_key:apiKey.value,daily_calls:null,max_output_tokens:Number(output.value),enabled:true,consent:consent.value,cache_enabled:cacheEnabled.value});await refreshStatus();broadcast();notice.value='密钥已按账户加密保存到数据库。退出登录或服务重启后保留；保存没有调用模型。'}catch(e){error.value=(e as Error).message}finally{apiKey.value='';configBusy.value=false}
}
async function verifyKey(){configBusy.value=true;error.value='';try{await jsonRequest('verify','POST',{});await refreshStatus();broadcast();notice.value='已完成模型列表检查，没有发送生成请求。'}catch(e){error.value=(e as Error).message;await refreshStatus()}finally{configBusy.value=false}}
async function disconnect(){configBusy.value=true;try{await jsonRequest('disconnect','POST',{});apiKey.value='';await refreshStatus();broadcast();notice.value='连接已停用，数据库中的密钥保留；在途请求将尝试取消，已发送的调用仍可能计费。'}catch(e){error.value=(e as Error).message}finally{configBusy.value=false}}
async function enableSaved(){configBusy.value=true;error.value='';try{await jsonRequest('enable','POST',{});await refreshStatus();broadcast();notice.value='已启用数据库保存的密钥；本操作没有调用模型。'}catch(e){error.value=(e as Error).message}finally{configBusy.value=false}}

async function testConnection(){
 if(!paidConsent.value||busy.value||configBusy.value||!status.value.enabled)return
 busy.value=true;paidConsent.value=false;error.value='';notice.value='';testController=new AbortController()
 const id=testId.value||crypto.randomUUID();testId.value=id
 try{
  testResult.value=await jsonRequest('test','POST',{confirm_paid:true,request_id:id,stream:false},AbortSignal.any([testController.signal,AbortSignal.timeout(110000)]))
  testId.value=null;notice.value='模型测试已完成，结果仅显示在设置页面，不写入任何聊天会话。'
 }catch(e){
  if((e as Error).name!=='AbortError'){
   testId.value=diagnosticRequestAfterError(id,e)
   error.value=(e as Error).message+(testId.value?'；未自动重新发送。再次读取将沿用原请求编号。':'；该测试已结束或未被接受，修正配置并重新确认后可发起新测试。')
  }
 }
 finally{busy.value=false;testController=null;await refreshStatus()}
}
async function cancelModelTest(){
 if(!testId.value)return
 try{const r=await jsonRequest('cancel','POST',{request_id:testId.value});notice.value='已请求取消，已发送的模型调用仍可能计费。';if(r.cancel_requested||['DONE','CANCELLED','FAILED','REJECTED','EXPIRED'].includes(r.state))testId.value=null;testController?.abort()}
 catch(e){error.value=(e as Error).message}
}
const cropOpen=ref(false),cropZoom=ref(3),cropPreview=ref('')
let cropImage:HTMLImageElement|null=null,cropX=0,cropY=0,cropDrag:{x:number,y:number}|null=null
function sourceNode<T extends HTMLElement>(id:string){return rootElement.value?.querySelector<T>('[data-source-id="'+id+'"]')}
async function openCrop(){cropOpen.value=true;cropImage=null;cropZoom.value=3;cropX=0;cropY=0;cropPreview.value='';await nextTick();drawCrop()}
function chooseAvatarFile(){sourceNode<HTMLInputElement>('cropFileInput')?.click()}
function resetCrop(){cropZoom.value=3;cropX=0;cropY=0;drawCrop()}
function cropParams(){if(!cropImage)return null;const scale=Math.max(40/cropImage.naturalWidth,40/cropImage.naturalHeight)*cropZoom.value;const w=cropImage.naturalWidth*scale,h=cropImage.naturalHeight*scale;cropX=Math.max(-(w-40)/2,Math.min((w-40)/2,cropX));cropY=Math.max(-(h-40)/2,Math.min((h-40)/2,cropY));return {w,h,x:(240-w)/2+cropX,y:(240-h)/2+cropY}}
function drawCrop(){const canvas=sourceNode<HTMLCanvasElement>('cropCanvas'),ctx=canvas?.getContext('2d');if(!ctx)return;ctx.clearRect(0,0,480,480);ctx.save();ctx.scale(2,2);ctx.fillStyle='#191923';ctx.fillRect(0,0,240,240);const p=cropParams();if(cropImage&&p)ctx.drawImage(cropImage,p.x,p.y,p.w,p.h);ctx.fillStyle='#05050ac4';ctx.fillRect(0,0,240,100);ctx.fillRect(0,100,100,40);ctx.fillRect(140,100,100,40);ctx.fillRect(0,140,240,100);ctx.strokeStyle='#c5b5e8';ctx.lineWidth=1.4;ctx.strokeRect(100.7,100.7,38.6,38.6);ctx.restore();if(cropImage&&p){const out=document.createElement('canvas');out.width=128;out.height=128;out.getContext('2d')?.drawImage(cropImage,(p.x-100)*3.2,(p.y-100)*3.2,p.w*3.2,p.h*3.2);cropPreview.value=out.toDataURL('image/png')}}
function loadAvatar(e:Event){const file=(e.target as HTMLInputElement).files?.[0];if(!file)return;if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>8*1024*1024){error.value='请选择 8MB 以内的 PNG、JPEG 或 WebP';return}const url=URL.createObjectURL(file),img=new Image();img.onload=()=>{URL.revokeObjectURL(url);if(img.naturalWidth*img.naturalHeight>40000000){error.value='图片尺寸过大';return}cropImage=img;resetCrop()};img.onerror=()=>{URL.revokeObjectURL(url);error.value='图片无法读取'};img.src=url}
function startCrop(e:PointerEvent){cropDrag={x:e.clientX,y:e.clientY};(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)}
function moveCrop(e:PointerEvent){if(!cropDrag)return;cropX+=e.clientX-cropDrag.x;cropY+=e.clientY-cropDrag.y;cropDrag={x:e.clientX,y:e.clientY};drawCrop()}
function stopCrop(){cropDrag=null}
const avatarBusy=ref(false)
async function applyAvatar(){if(!cropPreview.value||avatarBusy.value)return;avatarBusy.value=true;try{await persistAvatar(cropPreview.value);cropOpen.value=false;notice.value='头像已保存，重新登录后仍可恢复。'}catch(e){notice.value=(e as Error).message}finally{avatarBusy.value=false}}
async function removeAvatar(){if(avatarBusy.value)return;avatarBusy.value=true;try{await persistAvatar(null);notice.value='已移除保存的头像。'}catch(e){notice.value=(e as Error).message}finally{avatarBusy.value=false}}

function refreshRelevant(){if(settingsTab.value==='ai')refreshStatus()}
watch(settingsTab,()=>{apiKey.value='';paidConsent.value=false;cropOpen.value=false;error.value='';notice.value='';refreshRelevant()},{immediate:true})
onMounted(()=>{window.addEventListener('mi2-model-status',refreshRelevant);window.addEventListener('focus',refreshRelevant)})
onBeforeUnmount(()=>{apiKey.value='';testController?.abort();window.removeEventListener('mi2-model-status',refreshRelevant);window.removeEventListener('focus',refreshRelevant)})
</script>
<template><div ref="rootElement" class="prototype-ai account-settings-surface"><section aria-label="MovieInsight 全局设置页面" class="global-settings-page" :id="uid('globalSettingsPage')" data-source-id="globalSettingsPage">
<header class="gs-top">
<RouterLink :to="returnTo" class="gs-return" :id="uid('globalSettingsBack')" data-source-id="globalSettingsBack" >← 返回上一页</RouterLink>
<div class="gs-brand">MovieInsight <small>偏好与账户 · 全局设置</small></div>
<span class="gs-tag">个人偏好与模型连接</span>
</header>
<div class="gs-shell">
<nav aria-label="设置分类" class="gs-nav">
<button :class="{active:settingsTab==='account'}" @click="selectTab('account')" class="gs-tab" data-gs-tab="account">◉　个人资料</button>
<button :class="{active:settingsTab==='display'}" @click="selectTab('display')" class="gs-tab" data-gs-tab="display">◈　外观与布局</button>
<button :class="{active:settingsTab==='ai'}" @click="selectTab('ai')" class="gs-tab" data-gs-tab="ai">✧　AI 与 BYOK</button>
<button :class="{active:settingsTab==='tmdb'}" @click="selectTab('tmdb')" class="gs-tab" data-gs-tab="tmdb"><svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></svg>　TMDB 数据连接</button>
<button :class="{active:settingsTab==='privacy'}" @click="selectTab('privacy')" class="gs-tab" data-gs-tab="privacy">◇　隐私与数据</button>
<button :class="{active:settingsTab==='about'}" @click="selectTab('about')" class="gs-tab" data-gs-tab="about">ⓘ　关于 MovieInsight</button>
</nav>
<div class="gs-main">
<div class="gs-intro"><span class="gs-eyebrow">MOVIEINSIGHT</span><h1>全局设置</h1><p>这里集中管理整个 MovieInsight 的个人资料、外观、AI 服务及数据偏好。与聊天窗口设置分开，避免修改入口散落各处。</p></div>
<p v-if="error" class="settings-error" role="alert">{{error}}</p><p v-if="notice" class="gs-save-status" role="status">{{notice}}</p>
<section aria-label="个人资料设置" class="gs-panel" :id="uid('gs-account')" data-source-id="gs-account" v-show="settingsTab==='account'">
<div class="gs-card"><h2>头像与公开资料</h2><p>默认显示通用占位符，不预设头像。可选本地 PNG、WebP 或 JPG；选区限定在 40×40 的头像框，透明背景原样保留。</p>
<div class="gs-avatar-row"><span :aria-label="aiWorkspace.avatar?'当前账户头像':'未设置头像'" class="gs-avatar-swatch avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" v-if="!aiWorkspace.avatar" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="aiWorkspace.avatar" alt="" class="user-avatar-img" :id="uid('gsAvatarLarge')" data-source-id="gsAvatarLarge" v-if="aiWorkspace.avatar"/></span><div class="gs-avatar-caption"><strong>当前头像</strong><small>头像尺寸：40×40 px · 透明背景可保留</small><button @click="openCrop" class="gs-cta" :id="uid('editAvatarBtn')" data-source-id="editAvatarBtn" type="button">选择与裁剪头像</button><button @click="removeAvatar" class="gs-cta gs-cta-muted" :id="uid('resetAvatarBtn')" data-source-id="resetAvatarBtn" type="button">恢复占位符</button></div></div>
<div class="gs-formline"><label :for="uid('gsDisplayName')">显示名称</label><input :value="auth.user?.username" autocomplete="off" class="gs-input" :disabled="true" :id="uid('gsDisplayName')" data-source-id="gsDisplayName" maxlength="24" /></div>
<div class="gs-actions"><button class="gs-cta" :disabled="true" :id="uid('saveDisplayName')" data-source-id="saveDisplayName" type="button">账户名称由身份系统管理</button></div><div aria-live="polite" class="gs-save-status" :id="uid('accountStatus')" data-source-id="accountStatus">裁剪后保存到当前账户，退出或服务重启后保留；登录名称保持不变。</div>
</div>
<div class="gs-card"><h2>个人资料入口</h2><p>当前账户来自服务端登录身份；本页不修改认证机制。</p></div>
</section>
<section aria-label="全站外观与布局" class="gs-panel" :id="uid('gs-display')" data-source-id="gs-display" v-show="settingsTab==='display'">
<div class="gs-card"><h2>外观与布局</h2><p>沿用已确认的深色紫黑视觉，阅读与动效偏好同步到两个 AI 窗口。</p>
<div class="gs-setting-row"><div><h3>站点色彩</h3><p>V12-A / V13 深色紫黑品牌基线。</p></div><span class="gs-pill">保持原配色</span></div>
<div class="gs-setting-row"><div><h3>阅读字体</h3><p>影响 AI 消息文字与用户消息的阅读大小。</p></div><select :value="aiWorkspace.large?'large':'normal'" @change="aiWorkspace.large=($event.target as HTMLSelectElement).value==='large'" class="gs-select" :id="uid('gsTextSize')" data-source-id="gsTextSize"><option value="normal">标准</option><option value="large">较大</option></select></div>
<div class="gs-setting-row"><div><h3>动效偏好</h3><p>降低动画时精灵球与窗口过渡会减少动态效果。</p></div><select :value="aiWorkspace.reduced?'reduce':'normal'" @change="aiWorkspace.reduced=($event.target as HTMLSelectElement).value==='reduce'" class="gs-select" :id="uid('gsMotion')" data-source-id="gsMotion"><option value="normal">标准动画</option><option value="reduce">减少动态效果</option></select></div>
<div class="gs-setting-row"><div><h3>AI 默认打开方式</h3><p>默认悬浮大窗口；可在设置中改为全屏起始。</p></div><select :value="aiWorkspace.startFull?'fullscreen':'floating'" @change="aiWorkspace.startFull=($event.target as HTMLSelectElement).value==='fullscreen'" class="gs-select" :id="uid('gsStartMode')" data-source-id="gsStartMode"><option value="floating">悬浮窗口（当前默认）</option><option value="fullscreen">全屏工作区</option></select></div>
<div class="gs-setting-row"><div><h3>电影推荐卡片</h3><p>采用你已选定的 A 横向紧凑卡。</p></div><span class="gs-pill">A · 横向紧凑</span></div><div class="gs-setting-row"><div><h3>对话版式</h3><p>Gemini 阅读式偏长文阅读，MovieInsight 对话式偏即时问答。</p></div><select :value="dialogMode" @change="dialogMode=($event.target as HTMLSelectElement).value" class="gs-select" :id="uid('gsDialogMode')" data-source-id="gsDialogMode"><option value="gemini">Gemini 阅读式</option><option value="native">MovieInsight 对话式</option></select></div>
</div>
</section>
<section aria-label="全站 AI 与 BYOK 设置" class="gs-panel" :id="uid('gs-ai')" data-source-id="gs-ai" v-show="settingsTab==='ai'"><div class="gs-card"><h2>AI 服务与 BYOK</h2><p>密钥默认按账户加密保存到数据库，退出登录和服务重启后保留，注销账户时删除。模型配置共享，对话和草稿互相独立。</p><p v-if="!status.persistent" role="status">当前环境尚未启用数据库凭据服务，暂不能保存新密钥。</p><div class="settings-api"><form @submit.prevent="saveConfig" autocomplete="off">
<label :for="idPrefix+'-model'">DeepSeek 模型</label><select :id="idPrefix+'-model'" v-model="model" :disabled="configBusy||busy"><option value="deepseek-flash">DeepSeek Flash</option><option value="deepseek-v4-pro">DeepSeek V4 Pro（通常费用更高）</option></select>
<label :for="idPrefix+'-key'">API Key（不会回显或保存到浏览器）</label><input :id="idPrefix+'-key'" v-model="apiKey" type="password" autocomplete="off" spellcheck="false" autocapitalize="off" maxlength="512" required :disabled="configBusy||busy"/>
<div class="settings-grid"><p class="assistant-note">每日及每轮模型调用次数：不限（按你的设置）</p><label>每次最大输出 Token<input v-model.number="output" type="number" min="256" max="8192" required/></label></div>
<label class="check"><input v-model="consent" type="checkbox" required/>我同意向所选服务商发送对话和必要的电影/偏好信息；启用后提交智能请求可能产生费用。</label>
<label class="check"><input v-model="cacheEnabled" type="checkbox"/>允许5分钟内复用相同用户、上下文和电影/偏好版本的回答（默认关闭）</label><button type="submit" :disabled="configBusy||busy||!consent||!apiKey||!status.persistent">保存并启用个人模型</button>
</form>
<div class="assistant-controls"><button type="button" :disabled="configBusy||busy||!status.configured" @click="verifyKey">检查 Key 与模型列表</button><button v-if="status.configured&&!status.enabled&&status.persistent" type="button" :disabled="configBusy||busy" @click="enableSaved">启用已保存的密钥</button><button type="button" :disabled="configBusy||!status.enabled" @click="disconnect">停用连接（保留密钥）</button></div>
<p class="assistant-note">状态：{{status.verification||'DISCONNECTED'}}；今日已派发 {{status.daily_calls_used??0}} / {{status.daily_calls??'不限'}} 次（UTC日）。列表检查不发送生成请求。不设每日或每轮调用次数上限；仍受90秒超时、并发和上下文长度保护。金额未取得可靠账单时显示未知。</p>
<label class="check"><input v-model="paidConsent" type="checkbox"/>我明确同意发送一次可能计费的模型连接测试。</label><button type="button" :disabled="busy||configBusy||!paidConsent||!status.enabled" @click="testConnection">{{testId?'读取原模型测试（不重复派发）':'发送模型测试（可能计费）'}}</button><button v-if="busy" type="button" @click="cancelModelTest">取消本次测试</button><p v-if="testResult" class="assistant-note">{{testResult.answer}} · 输入 {{testResult.cost?.input_tokens??'未知'}} / 输出 {{testResult.cost?.output_tokens??'未知'}} Token · 金额{{testResult.cost?.amount===0?'0':'未知'}}</p>
</div></div></section>
<section aria-label="TMDB 数据连接设置" class="gs-panel" v-if="settingsTab==='tmdb'"><div class="gs-card"><TMDBSettings :id-prefix="idPrefix"/></div></section>
<section aria-label="隐私与数据设置" class="gs-panel" :id="uid('gs-privacy')" data-source-id="gs-privacy" v-show="settingsTab==='privacy'">
<div class="gs-card"><h2>隐私与数据</h2><p>可查看当前页面的数据范围与控制方式。</p>
<div class="gs-setting-row"><div><h3>聊天记录</h3><p>当前登录页面内保留；刷新后不自动恢复记录。</p></div><span class="gs-pill">本地临时</span></div>
<div class="gs-setting-row"><div><h3>头像图片</h3><p>仅上传裁剪后的头像，按账户保存，可随时移除。</p></div><span class="gs-pill">账户保存</span></div>
<div class="gs-setting-row"><div><h3>管理聊天显示记录</h3><p>两个窗口分别清空，仅移除页面内的记录，不删除服务端数据。</p></div><div class="display-actions"><button @click="clearDisplay('explore')" class="gs-cta gs-cta-muted" type="button">清空 AI 探索显示记录</button><button @click="clearDisplay('floating')" class="gs-cta gs-cta-muted" type="button">清空悬浮助手显示记录</button></div></div>
<div aria-live="polite" class="gs-save-status" :id="uid('privacyStatus')" data-source-id="privacyStatus"></div>
</div>
</section>
<AccountClosure v-if="settingsTab==='privacy'" :id-prefix="idPrefix"/>
<section aria-label="关于 MovieInsight" class="gs-panel" :id="uid('gs-about')" data-source-id="gs-about" v-show="settingsTab==='about'">
<div class="gs-card"><h2>关于 MovieInsight</h2><p>AI 驱动的电影发现工具。目录、个人行为与助手请求都由服务端校验。</p><div class="gs-divider"></div><p>用户自带模型 Key；电影资料与个人行为由服务端验证。</p><p>This product uses the TMDB API but is not endorsed or certified by TMDB.</p><p><a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer">电影数据来源：The Movie Database（TMDB）</a></p><div class="gs-notice" style="margin-top:16px">当前版本：M9 · 界面已接入真实目录与个人行为数据。</div></div>
</section>
</div>
</div>
</section>
<div aria-label="选择头像显示范围" aria-modal="true" class="crop-overlay" :id="uid('avatarCropOverlay')" data-source-id="avatarCropOverlay" role="dialog" v-if="cropOpen">
<div class="crop-dialog"><div class="crop-head"><div><h2>调整头像显示范围</h2><p>只有中心 40×40 像素的方框是头像选区。可拖动图片调整构图、缩放图片，框外部分不会被选中；PNG、WebP 透明区域不会填底。</p></div><button @click="cropOpen=false" aria-label="取消裁剪" class="crop-close" :id="uid('cropClose')" data-source-id="cropClose" type="button">×</button></div>
<div class="crop-content"><div class="crop-work"><div @pointercancel="stopCrop" @pointerdown="startCrop" @pointermove="moveCrop" @pointerup="stopCrop" class="crop-viewport" :id="uid('cropViewport')" data-source-id="cropViewport" style="touch-action:none"><canvas height="480" :id="uid('cropCanvas')" data-source-id="cropCanvas" width="480"></canvas></div><span style="font-size:11px;color:#a29aac">中央 40×40 取景框 · 可拖动底图</span></div><div class="crop-preview-block"><span>实际头像尺寸预览</span><span aria-label="头像预览" class="crop-preview avatar-slot is-empty"><svg aria-hidden="true" class="avatar-placeholder" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="1.55" viewBox="0 0 24 24"><circle cx="12" cy="8.2" r="3.45"></circle><path d="M5.5 19.1c.6-3.45 2.88-5.1 6.5-5.1s5.9 1.65 6.5 5.1"></path></svg><img :src="cropPreview" alt="" class="user-avatar-img" :id="uid('cropPreview')" data-source-id="cropPreview" v-if="cropPreview"/></span><strong style="font-size:12px;color:#e3dcf3">40 × 40 px · 与侧栏相同</strong><button @click="resetCrop" class="gs-cta gs-cta-muted" :id="uid('cropReset')" data-source-id="cropReset" type="button">恢复居中</button></div></div>
<div class="crop-controls"><label :for="uid('cropZoom')">缩放图片</label><input @input="drawCrop" :id="uid('cropZoom')" data-source-id="cropZoom" max="8" min="1" step="0.05" type="range" v-model.number="cropZoom"/></div>
<div class="crop-actions"><button @click="chooseAvatarFile" class="gs-cta gs-cta-muted" :id="uid('cropChooseFile')" data-source-id="cropChooseFile" type="button">选择本地图片</button><button @click="cropOpen=false" class="gs-cta gs-cta-muted" :id="uid('cropCancel')" data-source-id="cropCancel" type="button">取消</button><button @click="applyAvatar" :disabled="!cropPreview||avatarBusy" class="gs-cta" :id="uid('cropApply')" data-source-id="cropApply" type="button">保存头像</button></div>
<input @change="loadAvatar" accept="image/png,image/jpeg,image/webp" hidden="" :id="uid('cropFileInput')" data-source-id="cropFileInput" type="file"/>
</div>
</div></div></template>
<style scoped src="../styles/assistant-surface.css"></style>
<style scoped src="../styles/account-settings.css"></style>
<style scoped src="../styles/assistant-avatar.css"></style>
<style scoped>
.account-settings-surface{position:relative;height:100%;min-height:0}.gs-return{text-decoration:none}.display-actions{display:flex;flex-wrap:wrap;gap:8px}.settings-error{color:#ffc2cf;font-size:13px;line-height:1.7}
</style>
