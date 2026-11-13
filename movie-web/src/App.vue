<script setup lang="ts">
import {watch} from 'vue'
import {resetAIWorkspace} from './services/aiWorkspace'
import {useRoute} from 'vue-router'
import {useAuthStore} from './stores/auth'
import PosterWall from './components/PosterWall.vue'
import FloatingAssistant from './components/FloatingAssistant.vue'
const route=useRoute(),auth=useAuthStore()
watch(()=>auth.user?.id??0,id=>resetAIWorkspace(id),{immediate:true,flush:'sync'})
</script>
<template><PosterWall v-if="!['/recommendations','/home','/movies','/ai','/settings','/insights','/admin'].includes(route.path)"/><RouterView v-slot="{Component}"><Transition name="route"><KeepAlive :key="auth.user?.id??'anonymous'" include="AIExplore"><component :is="Component"/></KeepAlive></Transition></RouterView><FloatingAssistant v-if="auth.user&&route.path!=='/ai'" :key="auth.user.id"/></template>
