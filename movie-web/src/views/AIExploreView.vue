<script setup lang="ts">
import {ref} from 'vue'
import AppSidebar from '../components/AppSidebar.vue'
import AssistantChat from '../components/AssistantChat.vue'
defineOptions({name:'AIExplore'})
const expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
</script>
<template><div class="explore-page" :style="{'--nav-width':expanded?'280px':'52.5px'}"><AppSidebar @expanded-change="expanded=$event"/><main class="explore-chat"><AssistantChat surface="explore"/></main></div></template>
<style scoped>.explore-page{position:relative;z-index:1;background:#0c0d0f;color:#fff;min-height:100vh}.explore-chat{margin-left:var(--nav-width);height:100dvh;transition:margin-left .25s ease}@media(prefers-reduced-motion:reduce){.explore-chat{transition:none}}/* unslop 重排（探索页专用）：安静的放映厅，空状态是唯一亮点 */
.explore-chat :deep(.thread-inner){max-width:760px!important;gap:0!important}
.explore-chat :deep(.thread-inner:has(.empty-state)){justify-content:center!important;min-height:100%!important}
.explore-chat :deep(.empty-state){max-width:100%!important;padding:0!important;text-align:center!important}
.explore-chat :deep(.empty-orb){margin:0 auto 26px!important}
.explore-chat :deep(.empty-state h2){font-family:'Noto Serif SC','Source Han Serif SC','Songti SC',Georgia,serif!important;font-size:clamp(26px,2.8vw,38px)!important;line-height:1.12!important;font-weight:500!important;letter-spacing:-.02em!important;margin:0 0 12px!important}
.explore-chat :deep(.empty-state p){max-width:430px!important;margin:0 auto!important;font-size:15px!important;line-height:1.75!important;color:#fff!important}
.explore-chat :deep(.empty-suggestions){display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr))!important;justify-content:stretch!important;gap:10px!important;margin:34px 0 0!important;width:100%!important}
.explore-chat :deep(.empty-suggestions .followup){width:100%!important;min-height:46px!important;padding:10px 12px!important;text-align:center!important;border:1px solid rgba(255,255,255,.14)!important;border-radius:8px!important;background:transparent!important;color:#fff!important;font-size:12.5px!important;line-height:1.5!important;transition:border-color .16s,background-color .16s!important}
.explore-chat :deep(.empty-suggestions .followup:hover){border-color:rgba(255,255,255,.32)!important;background:rgba(255,255,255,.04)!important}
@media(max-width:820px){.explore-chat :deep(.empty-suggestions){grid-template-columns:1fr!important}.explore-chat :deep(.empty-suggestions .followup){min-height:44px!important}}
@media(prefers-reduced-motion:reduce){.explore-chat :deep(.empty-suggestions .followup){transition:none!important}}.explore-chat :deep(#explore-thread),.explore-chat :deep(.chat-main),.explore-chat :deep(.empty-state),.explore-chat :deep(.empty-suggestions){color:#fff!important}
/* 会话侧栏重新整理：整栏锁在窗口高度内，只有列表滚动，底部导入与资料固定 */
.explore-chat :deep(#explore-sidebar){height:100%!important;min-height:0!important;overflow:hidden!important}
.explore-chat :deep(.side-panel){height:100%!important;max-height:100%!important;min-height:0!important;display:flex!important;flex-direction:column!important;overflow:hidden!important}
.explore-chat :deep(.side-top){flex:0 0 auto!important}
.explore-chat :deep(.side-action),.explore-chat :deep(.side-search){flex:0 0 auto!important}
.explore-chat :deep(.side-list){flex:1 1 auto!important;min-height:0!important;overflow-y:auto!important;overscroll-behavior:contain!important}
.explore-chat :deep(.side-import){flex:0 0 auto!important}
.explore-chat :deep(.profile-panel){flex:0 0 auto!important;max-height:60%!important;overflow-y:auto!important}/* 会话侧栏整理：一列安静的目录，只有当前会话被点亮 */
.explore-chat :deep(.side-panel){padding:14px 12px 12px!important;gap:10px!important}
.explore-chat :deep(.side-top){display:flex!important;align-items:center!important;justify-content:space-between!important;margin:0 2px 2px!important}
.explore-chat :deep(.side-label){font-size:11px!important;letter-spacing:.06em!important;color:#fff!important;font-weight:500!important}
.explore-chat :deep(.side-collapse){width:24px!important;height:24px!important;border-radius:6px!important;color:rgba(255,255,255,.55)!important}
.explore-chat :deep(.side-collapse:hover){background:rgba(255,255,255,.06)!important;color:#fff!important}
.explore-chat :deep(.side-action){height:34px!important;border:1px solid rgba(255,255,255,.16)!important;border-radius:8px!important;background:transparent!important;color:#fff!important;font-size:12.5px!important;gap:7px!important;padding:0 10px!important}
.explore-chat :deep(.side-action:hover){border-color:rgba(255,255,255,.34)!important;background:rgba(255,255,255,.04)!important}
.explore-chat :deep(.side-search){height:32px!important;border:1px solid rgba(255,255,255,.1)!important;border-radius:8px!important;padding:0 9px!important;gap:7px!important;color:rgba(255,255,255,.55)!important}
.explore-chat :deep(.side-search input){font-size:12.5px!important;color:#fff!important;background:transparent!important}
.explore-chat :deep(.side-search input::placeholder){color:rgba(255,255,255,.45)!important}
.explore-chat :deep(.side-list){display:flex!important;flex-direction:column!important;gap:1px!important;padding:2px 0!important;margin:2px -4px 0!important}
.explore-chat :deep(.side-row){min-height:32px!important;border-radius:7px!important;padding:0 8px!important;color:rgba(255,255,255,.72)!important;font-size:12.5px!important}
.explore-chat :deep(.side-row:hover){background:rgba(255,255,255,.05)!important;color:#fff!important}
.explore-chat :deep(.side-row.active){background:rgba(255,255,255,.08)!important;color:#fff!important}
.explore-chat :deep(.side-title){font-size:12.5px!important;font-weight:400!important;text-align:left!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important}
.explore-chat :deep(.side-row.active .side-title){font-weight:500!important}
.explore-chat :deep(.side-more){color:rgba(255,255,255,.5)!important;border-radius:6px!important}
.explore-chat :deep(.side-more:hover){color:#fff!important;background:rgba(255,255,255,.08)!important}
.explore-chat :deep(.side-import){margin-top:auto!important;padding-top:10px!important;border-top:1px solid rgba(255,255,255,.08)!important}
.explore-chat :deep(.side-import-btn){height:30px!important;font-size:12px!important;color:rgba(255,255,255,.6)!important;gap:6px!important;padding:0 2px!important}
.explore-chat :deep(.side-import-btn:hover){color:#fff!important}
.explore-chat :deep(.side-list::-webkit-scrollbar){width:6px!important}
.explore-chat :deep(.side-list::-webkit-scrollbar-thumb){background:rgba(255,255,255,.14)!important;border-radius:3px!important}
.explore-chat :deep(.side-list::-webkit-scrollbar-track){background:transparent!important}
.explore-chat :deep(.side-panel :focus-visible){outline:2px solid rgba(200,164,106,.7)!important;outline-offset:1px!important}/* 输入区底色与对话区一致 */
.explore-chat :deep(.composer-zone){background:transparent!important;border-top:0!important;box-shadow:none!important}
.explore-chat :deep(.composer-wrap){background:transparent!important}/* 侧栏开关合并为一个：保留页头的切换按钮，隐藏侧栏内重复的收起按钮 */
.explore-chat :deep(.side-collapse){display:none!important}
.explore-chat :deep(.side-top){justify-content:flex-start!important}/* 侧栏开关合并：页头按钮常驻，侧栏内重复的收起按钮隐藏 */
.explore-chat :deep(.side-collapse){display:none!important}</style>
