"""Extract source markup/styles for audit; runtime adaptation lives in AssistantChat.vue."""
from pathlib import Path
import re, hashlib
from bs4 import BeautifulSoup

root=Path(__file__).resolve().parents[3]
source=root/'系统重建/MovieInsight2_M9_Codex_Code_Handoff_20261111/02_EXTRACTED_READABLE/ai/ai_fullpage.html'
target=root/'项目源码/movie-web/src/components/AssistantChat.vue'
html=source.read_text(encoding='utf-8')
soup=BeautifulSoup(html,'html.parser')
window=soup.find(id='chatWindow')
settings=soup.find(id='globalSettingsPage')
crop=soup.find(id='avatarCropOverlay')
symbols=soup.body.find('svg',recursive=False)
def node(key):return soup.find(id=key)
def attrs(key,**values):
 n=node(key)
 for k,v in values.items():n[k.replace('ON_','@').replace('BIND_',':')]=v
 return n
def content(key,markup):
 n=node(key);n.clear()
 for c in list(BeautifulSoup(markup,'html.parser').contents):n.append(c)
 return n
def event(key,code):attrs(key,ON_click=code)

window['class']=['window','show','full']
window[':data-dialog-mode']='dialogMode'
window['v-show']='!settings'
window.attrs.pop('aria-modal',None)
attrs('sidebarToggle',ON_click='sidebarOpen=!sidebarOpen')
attrs('sidebar',BIND_class="{'closed':!sidebarOpen}")
content('connectionStatus', '<span class="dot"></span>{{status.cloud_available?status.model+" 已连接":"BYOK 未连接 · 本地兜底"}}')
event('connectionStatus',"openSettings('ai')")
event('newTop','reset');event('newSide','reset')
node('newSide').string='＋ 开启新的电影对话'
node('layoutToggle').decompose()
attrs('fullToggle',ON_click="emit('toggle-full')",**{'v-if':"surface==='floating'"})
attrs('closeChat',ON_click="emit('close')",**{'v-if':"surface==='floating'"})
node('sessionList').insert_before(BeautifulSoup('<input class="mi17-history-search" v-model="sessionSearch" aria-label="搜索会话" placeholder="搜索会话"/>','html.parser'))
content('sessionList','''<div v-for="s in filteredSessions" :key="s.id" class="mi17-session-row" :class="{active:s.id===activeSession}"><button class="session" :disabled="busy" @click="chooseSession(s.id)">{{s.title}}</button><button class="mi17-session-menu-btn" :aria-label="'管理会话：'+s.title" @click="sessionMenu=sessionMenu===s.id?'':s.id">⋯</button><div v-if="sessionMenu===s.id" class="mi17-session-pop"><button :disabled="busy" @click="renameSession(s)">重命名</button><button :disabled="busy" @click="deleteSession(s.id)">移除此页记录</button></div></div>''')
node('sessionList').insert_after(BeautifulSoup('''<div><button class="mi28-shared-trigger" @click="sharedOpen=!sharedOpen">从另一窗口导入已保存记录</button><div v-if="sharedOpen" class="mi28-shared-tray"><div class="mi28-shared-heading">导入为独立记录，后续提问开启新模型上下文</div><button v-for="s in otherSessions" :key="s.id" class="mi28-shared-row" :disabled="busy" @click="importSession(s)"><strong>{{s.title}}</strong><span>{{s.history.length}} 条回答</span></button><p v-if="!otherSessions.length">另一窗口暂无记录。</p></div></div>''','html.parser'))
node('sidebar').select_one('.side-head span').string='当前登录会话'
attrs('profilePanel',BIND_hidden='!profileOpen')
event('profileEntry','profileOpen=!profileOpen');event('profileClose','profileOpen=false')
event('openAccountSettings',"openSettings('account')");event('accountSettingsEntry',"openSettings('account')")
for n in [*window.select('.account-name'),*window.select('.profile-meta strong')]:n.string='{{auth.user?.username}}'
window.select_one('.profile-detail').string='评分、收藏与想看由当前登录账户独立保存。'
window.select_one('.profile-note').string='头像仅在本次页面会话预览；账户名称由身份系统管理。'
attrs('mi15Gemini',ON_click="dialogMode='gemini'",BIND_class="{active:dialogMode==='gemini'}",**{':aria-pressed':"dialogMode==='gemini'"})
attrs('mi15Own',ON_click="dialogMode='native'",BIND_class="{active:dialogMode==='native'}",**{':aria-pressed':"dialogMode==='native'"})
window.select_one('.mi15-stylebar-label').string='对话版式'
node('mi15Gemini')['class']=['mi15-mode']
attrs('thread',ref='thread')
content('threadInner','<!-- THREAD_MARKUP -->')
attrs('messageInput',**{'v-model':'message',':disabled':'busy','maxlength':'2000','@keydown.enter.exact.prevent':'submit'})
attrs('composerForm',**{'@submit.prevent':'submit'})
event('attachBtn','shortcutsOpen=!shortcutsOpen')
attrs('composerShortcuts',BIND_hidden='!shortcutsOpen')
node('composerShortcuts').select_one('.shortcut-label').string='快捷提问'
for n in node('composerShortcuts').select('[data-shortcut]'):n['@click']='message='+repr(n['data-shortcut'])
event('modeChip',"openSettings('ai')")
node('modeChip').select_one('.mode-name').string='{{status.cloud_available?status.model:"本地兜底"}}'
attrs('sendBtn',**{':type':"busy?'button':'submit'",'@click':'busy&&cancel()',':disabled':'!busy&&!message.trim()',':aria-label':"busy?'取消生成':'发送消息'"})
content('sendBtn','''<svg class="ico"><use :href="'#'+uid(busy?'i-close':'i-arrow-up')"/></svg>''')
content('composerStatus','{{busy?"正在处理…":status.cloud_available?"使用你的服务商账户 · 可能计费":"本地检索 · 不调用模型"}}')
for key in ['detailPanel','settingsPanel','comparePop']:node(key).decompose()

# Keep the supplied settings page structure and tabs. Replace only demo claims and API controls.
settings['v-show']='settings';settings.attrs.pop('hidden',None)
event('globalSettingsBack',"settings=false;apiKey=''")
settings.select_one('.gs-tag').string='个人偏好与模型连接'
settings.select_one('.gs-eyebrow').string='MOVIEINSIGHT'
for n in settings.select('[data-gs-tab]'):
 tab=n['data-gs-tab'];n['@click']=f"settingsTab='{tab}'";n[':class']=f"{{active:settingsTab==='{tab}'}}";n['class']=['gs-tab']
for tab in ['account','display','ai','privacy','about']:
 n=node('gs-'+tab);n.attrs.pop('hidden',None);n['v-show']=f"settingsTab==='{tab}'"
node('gsDisplayName')[':value']='auth.user?.username';node('gsDisplayName')['disabled']=''
node('saveDisplayName')['disabled']='';node('saveDisplayName').string='账户名称由身份系统管理'
node('accountStatus').string='头像只在当前登录页面预览，不上传或改写账户资料。'
for n in settings.select('p'):
 text=n.get_text()
 if '真实用户 ID' in text:n.string='当前账户来自服务端登录身份；本页不修改认证机制。'
 if '研究稿提供' in text:n.string='沿用已确认的深色紫黑视觉，阅读与动效偏好同步到两个 AI 窗口。'
 if '研究稿没有发送' in text:n.string='连接状态来自现有个人模型接口。'
 if '本研究稿保留' in text:n.string='MovieInsight 电影智能探索。页面移植自用户提供的设计源码。'
 if 'M8 仍已结项' in text:n.string='用户自带模型 Key；电影资料与个人行为由服务端验证。'
 if '当前原型' in text:n.string='可查看当前页面的数据范围与控制方式。'
 if '只重置当前页面内模拟' in text:n.string='只移除此窗口内的显示记录，不删除服务端数据。'
 if '只在浏览器内裁剪' in text:n.string='仅在当前页面裁剪与预览，不上传、不长期持久化。'
 if '仅当前网页会话内存' in text:n.string='当前登录页面内保留；刷新后不自动恢复记录。'
settings.select('#gs-about h2').string='关于 MovieInsight'
settings.select('#gs-about .gs-notice').string='个人模型调用可能产生服务商费用。未连接时使用本地电影检索。'
settings.select('#gs-privacy .gs-pill').string='页面内存'
node('clearDemoSessions').string='清空此窗口显示记录';event('clearDemoSessions','clearSessions')
event('editAvatarBtn','openCrop');event('resetAvatarBtn',"aiWorkspace.avatar=''")
attrs('gsTextSize',**{':value':"aiWorkspace.large?'large':'normal'",'@change':"aiWorkspace.large=($event.target as HTMLSelectElement).value==='large'"})
attrs('gsMotion',**{':value':"aiWorkspace.reduced?'reduce':'normal'",'@change':"aiWorkspace.reduced=($event.target as HTMLSelectElement).value==='reduce'"})
attrs('gsStartMode',**{':value':"aiWorkspace.startFull?'fullscreen':'floating'",'@change':"aiWorkspace.startFull=($event.target as HTMLSelectElement).value==='fullscreen'"})
content('gs-ai','<div class="gs-card"><h2>AI 服务与 BYOK</h2><p>模型配置共享到两个窗口；对话和草稿互相独立。</p><!-- BYOK_MARKUP --></div>')
node('gsAiProvider') if node('gsAiProvider') else None

# The original crop UI remains; its canvas is wired to a bounded local-only adapter.
crop.attrs.pop('hidden',None);crop['v-if']='cropOpen'
for key in ['cropClose','cropCancel']:event(key,'cropOpen=false')
event('cropReset','resetCrop');event('cropChooseFile','chooseAvatarFile');event('cropApply','applyAvatar')
attrs('cropZoom',**{'v-model.number':'cropZoom','@input':'drawCrop'})
attrs('cropFileInput',**{'@change':'loadAvatar'})
attrs('cropViewport',**{'@pointerdown':'startCrop','@pointermove':'moveCrop','@pointerup':'stopCrop','@pointercancel':'stopCrop'})
node('cropViewport')['style']='touch-action:none'
for n in [*window.select('.user-avatar-img'),*settings.select('.user-avatar-img')]:
 n.attrs.pop('hidden',None);n['v-if']='aiWorkspace.avatar';n[':src']='aiWorkspace.avatar'
for n in [*window.select('.avatar-placeholder'),*settings.select('.avatar-placeholder')]:n['v-if']='!aiWorkspace.avatar'
node('cropPreview').attrs.pop('hidden',None);node('cropPreview')['v-if']='cropPreview';node('cropPreview')[':src']='cropPreview'

parts='\n'.join(str(n) for n in [symbols,window,settings,crop])
ids=re.findall(r'\bid="([^"]+)"',parts)
for value in ids:
 parts=parts.replace(f'id="{value}"',f':id="uid(\'{value}\')" data-source-id="{value}"')
 parts=parts.replace(f'for="{value}"',f':for="uid(\'{value}\')"')
 parts=parts.replace(f'href="#{value}"',f':href="\'#\'+uid(\'{value}\')"')
parts=re.sub(r'data:image/png;base64,[^"\']+', '/m9/assistant-orb.png',parts)
styles='\n'.join(n.get_text() for n in soup.find_all('style'))
for value in ids:styles=re.sub(r'#'+re.escape(value)+r'(?![\w-])',f'[data-source-id="{value}"]',styles)
styles=re.sub(r'(?<![\w-])(?:html|body)(?![\w-])', '.prototype-ai',styles)
styles=styles.replace(':root','.prototype-ai')
# No visual redesign: only replace iframe viewport positioning with component bounds.
styles+='''
.prototype-ai{position:relative;width:100%;height:100%;overflow:hidden;isolation:isolate}
.prototype-ai .window,.prototype-ai .window.show,.prototype-ai .window.full{position:absolute!important;inset:0!important;left:0!important;top:0!important;width:100%!important;height:100%!important;max-width:none!important;max-height:none!important;min-width:0!important;min-height:0!important;transform:none!important;opacity:1!important;visibility:visible!important;border-radius:0!important;box-shadow:none!important;transition:none!important}
.prototype-ai.surface-floating .window{background:transparent!important;border:0!important}
.prototype-ai .global-settings-page{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;min-height:0!important;overflow:auto!important;z-index:40}
.prototype-ai .crop-overlay{position:absolute!important;inset:0!important}
.prototype-ai .mi15-stylebar{flex:none}.prototype-ai .mi17-history-search{width:calc(100% - 20px);margin:4px 10px 12px;background:#171420;color:#eee5fa;border:1px solid #ffffff20;border-radius:9px;padding:10px;font:inherit;font-size:12px}
.prototype-ai .reply-body{white-space:pre-wrap;overflow-wrap:anywhere}.prototype-ai .poster{cursor:pointer}.prototype-ai .poster-uploaded{opacity:1!important;position:absolute;inset:0;width:100%;height:100%;object-fit:cover}.prototype-ai .reply-status{color:#c9b5dd;font-size:11px;line-height:1.8}
.prototype-ai .settings-api label{display:block;margin:16px 0 8px;font-size:12px;color:#c9bfd5}.prototype-ai .settings-api :is(input:not([type=checkbox]),select){width:100%;max-width:450px;min-width:0;background:#0e0e15;border:1px solid #ffffff25;border-radius:11px;padding:12px;color:#f4efff;font:inherit}.prototype-ai .settings-api .check{display:flex;gap:10px;align-items:start;line-height:1.8}.prototype-ai .settings-api button{border:1px solid #9d88d04f;background:#8d6bc42c;border-radius:10px;padding:10px 14px;color:#efe8fc;font-size:12px}.prototype-ai .settings-api .assistant-controls{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.prototype-ai .settings-api .assistant-note{font-size:11px;color:#b0a5c0;line-height:1.9}
.prototype-ai [hidden]{display:none!important}.prototype-ai button:disabled{opacity:.45;cursor:default}
'''
evidence=root/'项目工作日志/06-开发与验证/M9-AI源码直接移植-20261111'
(evidence/'source-template.fragment').write_text(parts,encoding='utf-8')
(evidence/'source-styles.fragment').write_text(styles,encoding='utf-8')
(evidence/'source-sha256.txt').write_text(hashlib.sha256(source.read_bytes()).hexdigest(),encoding='utf-8')
print('Extracted original AI structure and',len(styles),'CSS characters; no demo scripts copied.')
