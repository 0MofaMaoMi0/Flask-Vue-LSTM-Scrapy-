<script setup lang="ts">
import {ref} from 'vue'
import M9Sidebar from '../components/M9Sidebar.vue'
import AssistantChat from '../components/AssistantChat.vue'
defineOptions({name:'AIExplore'})
const expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
</script>
<template><div class="explore-page" :style="{'--nav-width':expanded?'280px':'52.5px'}"><M9Sidebar @expanded-change="expanded=$event"/><main class="explore-chat"><AssistantChat surface="explore"/></main></div></template>
<style scoped>.explore-page{position:relative;z-index:1;background:#080a13;color:#f2eff9;min-height:100vh}.explore-chat{margin-left:var(--nav-width);height:100dvh;transition:margin-left .25s ease}</style>
