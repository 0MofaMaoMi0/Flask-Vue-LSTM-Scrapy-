<script setup lang="ts">
import {ref} from 'vue'
import AdminImports from '../AdminImports.vue'
import DataQualityPanel from './DataQualityPanel.vue'
import MovieAssetsPanel from './MovieAssetsPanel.vue'
import './admin-ui.css'

const props = defineProps<{ country?: string; countryLabel?: string }>()
const emit = defineEmits<{openMovie: [id: number]}>()
const assets = ref<InstanceType<typeof MovieAssetsPanel>>()
const quality = ref<InstanceType<typeof DataQualityPanel>>()
const imports = ref<InstanceType<typeof AdminImports>>()

function refresh() {
  assets.value?.refresh()
  quality.value?.refresh()
  imports.value?.refresh()
}
defineExpose({refresh})
</script>

<template>
  <div class="catalog-panel">
    <DataQualityPanel ref="quality" />
    <MovieAssetsPanel ref="assets" :country="props.country" :country-label="props.countryLabel" @open-movie="emit('openMovie', $event)"/>
    <AdminImports ref="imports" />
  </div>
</template>

<style scoped>
.catalog-panel{min-width:0}
</style>