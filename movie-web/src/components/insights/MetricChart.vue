<script setup lang="ts">
import {ref,watch,onMounted,onBeforeUnmount} from 'vue'
import {use,init,type EChartsType} from 'echarts/core'
import {BarChart,LineChart} from 'echarts/charts'
import {GridComponent,TooltipComponent,AriaComponent} from 'echarts/components'
import {CanvasRenderer} from 'echarts/renderers'
import {isLightTheme,siteTheme} from '../../services/theme'
use([BarChart,LineChart,GridComponent,TooltipComponent,AriaComponent,CanvasRenderer])
const props=defineProps<{title:string;rows:{label:string;value:number|null}[];line?:boolean;stacks?:{name:string;values:(number|null)[]}[]}>()
const host=ref<HTMLElement>();let chart:EChartsType|undefined,observer:ResizeObserver|undefined
function ensureChart(){if(chart)return true;if(!host.value||!props.rows.length)return false;chart=init(host.value);observer=new ResizeObserver(()=>chart?.resize());observer.observe(host.value);return true}
function draw(){if(!props.rows.length||!ensureChart())return;const light=isLightTheme.value;const accent=light?'#8C6226':'#c8a46a';const axisText=light?'#000000':'#FFFFFF';const axisLine=light?'rgba(31,27,22,.18)':'rgba(241,236,226,.18)';const splitLine=light?'rgba(31,27,22,.08)':'rgba(241,236,226,.08)';const palette=light?['#8C6226','#4E6E81','#5C7D68']:['#c8a46a','#5b8fa8','#6aa88d'];chart!.setOption({animation:false,aria:{enabled:true},tooltip:{trigger:'axis',backgroundColor:light?'#FFFFFF':'#141519',borderColor:light?'rgba(31,27,22,.16)':'rgba(241,236,226,.22)',textStyle:{color:light?'#000000':'#FFFFFF',fontSize:12}},grid:{left:65,right:20,bottom:65,top:20},xAxis:{type:'category',data:props.rows.map(x=>x.label),axisLabel:{rotate:25,color:axisText,fontSize:11},axisLine:{lineStyle:{color:axisLine}},axisTick:{lineStyle:{color:axisLine}}},yAxis:{type:'value',min:0,axisLabel:{color:axisText,fontSize:11},axisLine:{show:false},splitLine:{lineStyle:{color:splitLine}}},series:props.stacks?.map((s,i)=>({name:s.name,type:'bar',stack:'tokens',data:s.values,itemStyle:{color:palette[i]}}))||[{type:props.line?'line':'bar',data:props.rows.map(x=>x.value),itemStyle:{color:accent},...(props.line?{lineStyle:{color:accent,width:2}}:{})}]},true);chart!.resize()}
onMounted(draw)
watch([()=>props.rows,siteTheme],draw,{deep:true,flush:'post'});onBeforeUnmount(()=>{observer?.disconnect();chart?.dispose();chart=undefined})
</script>
<template><section class="chart"><h3>{{title}}</h3><div v-show="rows.length" ref="host" class="plot" role="img" :aria-label="title"/><p v-show="!rows.length">当前范围暂无数据。</p><table><caption>{{title}} · 等价数据表</caption><thead><tr><th>维度</th><th>数值</th></tr></thead><tbody><tr v-for="r in rows" :key="r.label"><td>{{r.label}}</td><td>{{r.value??'未知'}}</td></tr></tbody></table></section></template>
<style scoped>h3{font-size:13px;font-weight:500;color:var(--mi-text);margin:16px 0 8px}p{color:var(--mi-text-muted);font-size:12.5px;margin:8px 0}.plot{width:100%;height:260px}table{border-collapse:collapse;width:100%;font-size:12.5px;color:var(--mi-text-soft);font-variant-numeric:tabular-nums;margin-top:8px}th,td{text-align:left;border-bottom:1px solid var(--mi-border-subtle);padding:7px 8px}th{color:var(--mi-text-muted);font-weight:500;font-size:11px;background:var(--mi-surface-raised)}caption{text-align:left;color:var(--mi-text-subtle);font-size:11px;padding:4px 0}.chart{min-width:0}</style>

