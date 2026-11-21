<script setup lang="ts">
import {nextTick, onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {init, use, type EChartsType} from 'echarts/core'
import {ScatterChart} from 'echarts/charts'
import {AriaComponent, GridComponent, TooltipComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
import {isLightTheme, siteTheme} from '../../services/theme'

use([ScatterChart, AriaComponent, GridComponent, TooltipComponent, CanvasRenderer])

export interface ScatterPoint { x: number; y: number; label: string; meta?: string }
const props = defineProps<{ points: ScatterPoint[]; label: string; xName: string; yName: string }>()
const host = ref<HTMLDivElement>(), failed = ref(false)
let chart: EChartsType | undefined, observer: ResizeObserver | undefined, frame = 0

function draw() {
  if (!chart || !host.value) return
  const light = isLightTheme.value
  const labelInk = light ? '#000000' : '#FFFFFF'
  const gridLine = light ? 'rgba(31,27,22,.08)' : '#ffffff08'
  const axisLine = light ? 'rgba(31,27,22,.18)' : '#ffffff15'
  chart.setOption({
    animation: false,
    backgroundColor: 'transparent',
    aria: {enabled: true, label: {description: props.label}},
    grid: {left: 12, right: 16, top: 16, bottom: 26, containLabel: true},
    tooltip: {
      trigger: 'item', confine: true,
      formatter: (params: any) => {
        const point = props.points[params.dataIndex]
        if (!point) return ''
        return `${point.label}\n${props.xName}: ${point.x}\n${props.yName}: ${point.y}${point.meta ? '\n' + point.meta : ''}`
      },
      backgroundColor: light ? '#FFFFFF' : '#1c192b',
      borderColor: light ? 'rgba(31,27,22,.16)' : '#6d5a8c',
      borderWidth: 1,
      padding: [8, 11],
      textStyle: {color: light ? '#000000' : '#FFFFFF', fontSize: 11, lineHeight: 18},
    },
    xAxis: {
      type: 'value', name: props.xName, nameLocation: 'middle', nameGap: 20,
      nameTextStyle: {color: labelInk, fontSize: 10},
      min: 0, max: 10, splitLine: {lineStyle: {color: gridLine}},
      axisLabel: {color: labelInk, fontSize: 10}, axisLine: {lineStyle: {color: axisLine}},
    },
    yAxis: {
      type: 'value', name: props.yName, nameTextStyle: {color: labelInk, fontSize: 10},
      splitLine: {lineStyle: {color: gridLine}},
      axisLabel: {color: labelInk, fontSize: 10}, axisLine: {lineStyle: {color: axisLine}},
    },
    series: [{
      type: 'scatter', symbolSize: 7,
      itemStyle: {color: light ? 'rgba(140,98,38,.72)' : 'rgba(185,160,244,.72)', borderColor: light ? '#754F1B' : '#cbb3f3', borderWidth: .5},
      emphasis: {itemStyle: {color: light ? '#1F1B16' : '#d8c3f8'}},
      data: props.points.map(point => [point.x, point.y]),
    }],
  })
}
function resize() { cancelAnimationFrame(frame); frame = requestAnimationFrame(() => chart?.resize()) }
onMounted(async () => {
  await nextTick()
  if (!host.value) return
  try {
    chart = init(host.value, undefined, {renderer: 'canvas'})
    observer = new ResizeObserver(resize)
    observer.observe(host.value)
    draw()
  } catch { failed.value = true }
})
watch([() => props.points, siteTheme], draw, {deep: false})
onBeforeUnmount(() => { cancelAnimationFrame(frame); observer?.disconnect(); chart?.dispose() })
</script>

<template>
  <div class="admin-scatter">
    <div v-if="!props.points.length" class="adm-empty">当前筛选下没有可绘制的评分 × 热度样本。</div>
    <div v-else-if="failed" class="adm-empty">图表组件暂不可用。</div>
    <div v-else ref="host" class="scatter-host" role="img" :aria-label="label"/>
    <p v-if="props.points.length" class="adm-note">{{ label }} · 共 {{ props.points.length }} 个样本；仅当前明细页，不代表全部影片。</p>
  </div>
</template>

<style scoped>
.admin-scatter{min-width:0}
.scatter-host{width:100%;height:260px}
</style>