<script setup lang="ts">
import {ref,watch,onMounted,onBeforeUnmount} from 'vue'
import {use,init,type EChartsType} from 'echarts/core'
import {BarChart,LineChart} from 'echarts/charts'
import {GridComponent,TooltipComponent,AriaComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
use([BarChart,LineChart,GridComponent,TooltipComponent,AriaComponent,CanvasRenderer])
const props=defineProps<{title:string;rows:{label:string;value:number|null}[];line?:boolean;stacks?:{name:string;values:(number|null)[]}[]}>()
const host=ref<HTMLElement>();let chart:EChartsType|undefined,observer:ResizeObserver|undefined
function draw(){chart?.setOption({animation:false,aria:{enabled:true},tooltip:{trigger:'axis'},grid:{left:65,right:20,bottom:65,top:20},xAxis:{type:'category',data:props.rows.map(x=>x.label),axisLabel:{rotate:25}},yAxis:{type:'value',min:0},series:props.stacks?.map((s,i)=>({name:s.name,type:'bar',stack:'tokens',data:s.values,itemStyle:{color:['#32769a','#6aa88d','#c38a44'][i]}}))||[{type:props.line?'line':'bar',data:props.rows.map(x=>x.value),itemStyle:{color:'#32769a'}}]},true)}
onMounted(()=>{if(host.value){chart=init(host.value);observer=new ResizeObserver(()=>chart?.resize());observer.observe(host.value);draw()}})
watch(()=>props.rows,draw,{deep:true});onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose()})
</script>
<template><section class="chart"><h3>{{title}}</h3><div v-if="rows.length" ref="host" class="plot" role="img" :aria-label="title"/><p v-else>当前范围暂无数据。</p><table><caption>{{title}} · 等价数据表</caption><thead><tr><th>维度</th><th>数值</th></tr></thead><tbody><tr v-for="r in rows" :key="r.label"><td>{{r.label}}</td><td>{{r.value??'未知'}}</td></tr></tbody></table></section></template>
<style scoped>.plot{width:100%;height:260px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;border-bottom:1px solid #d4dce1;padding:8px}caption{text-align:left;color:#52626d}.chart{min-width:0}</style>

