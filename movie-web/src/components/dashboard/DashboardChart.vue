<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { init, use, type EChartsType } from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { AriaComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

use([BarChart, LineChart, PieChart, AriaComponent, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{
  option: Record<string, any>
  label: string
  rows: { name: string; value: number | null }[]
  unit?: string
}>()

const host = ref<HTMLElement>()
const renderError = ref(false)
const hasKnownValues = computed(() => props.rows.some(row => typeof row.value === 'number' && Number.isFinite(row.value)))
const emptyMessage = computed(() => props.rows.length ? '当前指标数值暂不可得。' : '当前统计范围暂无数据。')
const numberFormat = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 })

let chart: EChartsType | undefined
let observer: ResizeObserver | undefined
let motionPreference: MediaQueryList | undefined
let resizeFrame = 0
let mounted = false

function display(value: number | null) {
  return typeof value === 'number' && Number.isFinite(value)
    ? numberFormat.format(value) + (props.unit ? ` ${props.unit}` : '')
    : '未知'
}

function draw() {
  if (!mounted || !host.value || !hasKnownValues.value) {
    chart?.clear()
    return
  }
  // v-show and a newly expanded panel can make the first observed size zero.
  // ResizeObserver will retry once the plot has a real layout size.
  if (!host.value.clientWidth || !host.value.clientHeight) return
  try {
    if (!chart) chart = init(host.value, undefined, { renderer: 'canvas' })
    const tooltipDefaults = { renderMode: 'richText', backgroundColor: '#201a30', borderColor: '#66517f', textStyle: { color: '#e9e4f4' } }
    const tooltip = Array.isArray(props.option.tooltip)
      ? props.option.tooltip.map(item => ({ ...tooltipDefaults, ...item, renderMode: 'richText' }))
      : { ...tooltipDefaults, ...props.option.tooltip, renderMode: 'richText' }
    chart.setOption({
      backgroundColor: 'transparent',
      color: ['#b9a0f4', '#8770bc', '#d3bb91', '#9aafcb', '#b58da8'],
      textStyle: { color: '#aaa2bb', fontFamily: getComputedStyle(host.value).fontFamily, fontSize: 11 },
      animationDuration: 420,
      animationDurationUpdate: 240,
      ...props.option,
      tooltip,
      animation: motionPreference?.matches ? false : (props.option.animation ?? true),
      aria: { ...props.option.aria, enabled: true, label: { enabled: true, description: props.label } },
    }, { notMerge: true, lazyUpdate: true })
    renderError.value = false
  } catch {
    // The equivalent data table remains available if canvas rendering fails.
    renderError.value = true
  }
}

function resize() {
  cancelAnimationFrame(resizeFrame)
  resizeFrame = requestAnimationFrame(() => {
    if (!mounted || !host.value?.clientWidth || !host.value.clientHeight) return
    chart?.resize()
    if (!chart) draw()
  })
}

onMounted(async () => {
  mounted = true
  motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  motionPreference.addEventListener('change', draw)
  await nextTick()
  if (!mounted || !host.value) return
  observer = new ResizeObserver(resize)
  observer.observe(host.value)
  draw()
})

watch([() => props.option, () => props.rows], () => {
  renderError.value = false
  nextTick(draw)
}, { deep: true, flush: 'post' })

onBeforeUnmount(() => {
  mounted = false
  cancelAnimationFrame(resizeFrame)
  observer?.disconnect()
  motionPreference?.removeEventListener('change', draw)
  chart?.dispose()
  chart = undefined
})
</script>

<template>
  <div class="dashboard-chart" data-chart>
    <div
      v-show="hasKnownValues && !renderError"
      ref="host"
      class="chart-plot"
      role="img"
      :aria-label="label"
    />
    <p v-if="!hasKnownValues" class="chart-state">{{ emptyMessage }}</p>
    <p v-else-if="renderError" class="chart-state" role="status">图表暂时无法呈现，可展开数据表查看。</p>
    <details class="chart-data">
      <summary>查看数据<span class="sr-only">：{{ label }}</span></summary>
      <div class="data-scroll" tabindex="0" role="region" :aria-label="label + '数据表'">
        <table>
          <caption class="sr-only">{{ label }} · 等价数据表</caption>
          <thead><tr><th scope="col">维度</th><th scope="col">{{ unit ? `数值（${unit}）` : '数值' }}</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in rows" :key="index"><th scope="row">{{ row.name }}</th><td>{{ display(row.value) }}</td></tr>
            <tr v-if="!rows.length"><td colspan="2">当前统计范围暂无数据。</td></tr>
          </tbody>
        </table>
      </div>
    </details>
  </div>
</template>

<style scoped>
.dashboard-chart{position:relative;display:flex;flex-direction:column;min-width:0;min-height:0;height:100%;color:#e9e4f4}
.chart-plot{width:100%;height:auto;min-height:110px;flex:1 1 0}
.chart-state{display:grid;place-items:center;flex:1;min-height:110px;margin:0;padding:20px;text-align:center;color:#aaa2bb;font-size:12px;line-height:1.7}
.chart-data{flex:none;margin-top:3px;color:#aaa2bb;font-size:11px;line-height:1.6}
.chart-data summary{width:fit-content;padding:3px 0;cursor:pointer;text-underline-offset:3px}
.chart-data summary:hover{color:#d7c6ff;text-decoration:underline}
.chart-data[open] summary{color:#d7c6ff}
.data-scroll{position:absolute;right:0;bottom:28px;left:0;z-index:4;max-height:min(180px,100%);overflow:auto;padding:6px 10px;background:#191524;border:1px solid #655278;border-radius:9px;scrollbar-width:thin;scrollbar-color:#795e9d #191524}
.chart-data:not([open]) .data-scroll{display:none}
table{width:100%;border-collapse:collapse;color:#e9e4f4;font-size:11px;line-height:1.6;text-align:left}
th,td{padding:7px 4px;border-bottom:1px solid #ffffff14;overflow-wrap:anywhere}
thead th{color:#c0b6d1;font-weight:600}
tbody th{font-weight:400}
td:last-child,thead th:last-child{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tbody tr:last-child th,tbody tr:last-child td{border-bottom:0}
summary:focus-visible,.data-scroll:focus-visible{outline:2px solid #b9a0f4;outline-offset:3px;border-radius:3px}
::selection{color:#fff;background:#62498d}
.sr-only{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
</style>
