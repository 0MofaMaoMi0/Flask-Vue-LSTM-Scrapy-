<script setup lang="ts">
import {ref} from 'vue'
import AppSidebar from '../components/AppSidebar.vue'
import GlobalSettings from '../components/GlobalSettings.vue'
const expanded=ref(localStorage.getItem('mi2_nav_expanded')==='true')
</script>
<template><div class="settings-page" :style="{'--nav-width':expanded?'280px':'52.5px'}"><AppSidebar @expanded-change="expanded=$event"/><main class="settings-content"><GlobalSettings/></main></div></template>
<style scoped>.settings-page{background:#0c0d0f;color:#fff;height:100dvh;overflow:hidden}.settings-content{margin-left:var(--nav-width);height:100%;min-width:0;transition:margin-left .25s ease}@media(prefers-reduced-motion:reduce){.settings-content{transition:none}}</style>
