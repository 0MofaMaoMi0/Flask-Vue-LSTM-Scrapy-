<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {init,registerMap,use,type EChartsType} from 'echarts/core'
import {MapChart} from 'echarts/charts'
import {AriaComponent,TooltipComponent,VisualMapComponent} from 'echarts/components'
import {SVGRenderer} from 'echarts/renderers'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {countryName} from '../../lib/globe/countryDisplay.js'
import {isLightTheme,siteTheme} from '../../services/theme'

use([MapChart,TooltipComponent,VisualMapComponent,AriaComponent,SVGRenderer])

type CountryRow={iso2:string;count:number;name?:string;value?:number;samples?:number}
type BoundaryFeature={type:'Feature';properties:{iso2?:string;iso3?:string;name?:string};geometry:unknown}
type BoundaryCollection={type:'FeatureCollection';features:BoundaryFeature[]}
const props=withDefaults(defineProps<{
  countries:CountryRow[]
  status?:string
  metric?:'count'|'rating'|'coverage'
  scaleMin?:number
  scaleMax?:number
}>(),{status:'ready',metric:'count',scaleMin:0,scaleMax:undefined})
const emit=defineEmits<{select:[iso2:string]}>()
const mapHost=ref<HTMLDivElement>(),loading=ref(true),error=ref(''),boundaryCodes=ref<string[]>([]),boundaryByIso2=ref<Record<string,string>>({})
let chart:EChartsType|undefined,observer:ResizeObserver|undefined,controller:AbortController|undefined
let disposed=false,drawn=false
let boundaries:BoundaryFeature[]=[]
const MAP_NAME='mi2-dashboard-world'
const SERIES_ID='catalog-countries'
const COUNTRY_CODES=ISO2_TO_ISO3 as Record<string,string>
// layoutSize percentages use the shorter viewport side; a wide, shallow panel
// needs a containing layout box to use its width without stretching geometry.
const mapLayout={left:8,right:8,top:10,bottom:10,aspectScale:1,preserveAspect:'contain' as const}

const state=computed(()=>props.status.toUpperCase())
const dataPending=computed(()=>['LOADING','PENDING'].includes(state.value))
const dataUnavailable=computed(()=>['ERROR','UNAVAILABLE','UNKNOWN'].includes(state.value))
const rows=computed(()=>{
  const unique=new Map<string,{iso2:string;iso3:string;name:string;count:number;value:number;samples:number}>()
  for(const row of props.countries){
    const iso2=typeof row.iso2==='string'?row.iso2.trim().toUpperCase():''
    const iso3=boundaryByIso2.value[iso2]||COUNTRY_CODES[iso2]
    if(!iso3||!Number.isFinite(row.count)||row.count<0)continue
    const value=typeof row.value==='number'&&Number.isFinite(row.value)?row.value:row.count
    unique.set(iso3,{iso2,iso3,name:countryName(COUNTRY_CODES[iso2]||iso3,row.name||iso2),count:Math.floor(row.count),value,samples:row.samples||0})
  }
  return [...unique.values()].sort((a,b)=>b.value-a.value||a.iso3.localeCompare(b.iso3))
})
const maxValue=computed(()=>Math.max(0,...rows.value.map(row=>row.value)))
const metricLabel=computed(()=>props.metric==='rating'?'有效平均 TMDB 评分':props.metric==='coverage'?'资料覆盖率':'影片数')
const legendMin=computed(()=>props.metric==='count'?'0':props.metric==='rating'?'0 分':'0%')
const legendMax=computed(()=>{
  if(props.metric==='rating')return (props.scaleMax??10).toFixed(0)+' 分'
  if(props.metric==='coverage')return ((props.scaleMax??1)*100).toFixed(0)+'%'
  return maxValue.value.toLocaleString('zh-CN')+' 部'
})
const description=computed(()=>dataUnavailable.value?'制作国家统计暂不可用。':dataPending.value?'正在读取制作国家统计。':rows.value.some(row=>row.value>0)?`世界电影${metricLabel.value}分布，${rows.value.filter(row=>row.value>0).length} 个制作国家或地区有影片。合拍影片可计入多个国家，统计按国家内电影 ID 去重。`:'当前没有可定位的制作国家资料。')
const unmappedCount=computed(()=>{
  const supported=new Set(boundaryCodes.value)
  return rows.value.filter(row=>row.count>0&&!supported.has(row.iso3)).length
})

function values(){
  const values=new Map(rows.value.map(row=>[row.iso3,row.value]))
  return boundaries.map(feature=>({name:feature.properties.iso3!,value:values.get(feature.properties.iso3!)}))
}
function tooltip(params:{name?:string}){
  if(dataPending.value||dataUnavailable.value)return description.value
  const iso3=params.name||''
  const row=rows.value.find(item=>item.iso3===iso3)
  const properties=boundaries.find(feature=>feature.properties.iso3===iso3)?.properties
  const name=row?.name||countryName(COUNTRY_CODES[properties?.iso2||'']||iso3,properties?.name||iso3)
  if(!row)return `${name}\n暂无该指标的可用样本（不按 0 计）`
  if(props.metric==='rating')return `${name}\n${row.value.toFixed(1)} 分 · ${row.samples.toLocaleString('zh-CN')} 个达标样本`
  if(props.metric==='coverage')return `${name}\n资料覆盖率 ${(row.value*100).toFixed(1)}% · ${row.count.toLocaleString('zh-CN')} 部影片`
  return `${name}\n${row.value.toLocaleString('zh-CN')} 部影片`
}
function draw(){
  if(!chart)return
  const min=props.metric==='count'?0:(props.scaleMin??0)
  const max=props.metric==='count'?Math.max(1,maxValue.value):(props.scaleMax??Math.max(1,maxValue.value))
  const light=isLightTheme.value
  const rangeColors=light?['#E6DFD1','#CEB893','#AD8549','#8C6226']:['#292235','#4b3967','#77569e','#b9a0f4']
  const areaBase=light?'#EDE6D8':'#292235'
  const borderBase=light?'#C4BAA8':'#756587'
  const hoverArea=light?'#A87D3C':'#c5abea'
  const selectArea=light?'#1F1B16':'#d3bb91'
  const tooltipOpt=light
    ?{trigger:'item',renderMode:'richText',confine:true,formatter:tooltip,backgroundColor:'#FFFFFF',borderColor:'rgba(31,27,22,.16)',borderWidth:1,padding:[9,12],textStyle:{color:'#000000',fontSize:12,lineHeight:20}}
    :{trigger:'item',renderMode:'richText',confine:true,formatter:tooltip,backgroundColor:'#1c192b',borderColor:'#6d5a8c',borderWidth:1,padding:[9,12],textStyle:{color:'#FFFFFF',fontSize:12,lineHeight:20}}
  if(drawn){
    chart.setOption({aria:{label:{description:description.value}},tooltip:tooltipOpt,visualMap:{min,max,inRange:{color:rangeColors}},series:[{id:SERIES_ID,itemStyle:{areaColor:areaBase,borderColor:borderBase,borderWidth:.55},emphasis:{label:{show:false},itemStyle:{areaColor:hoverArea,borderColor:light?'#754F1B':'#e5d6fa',borderWidth:1}},select:{label:{show:false},itemStyle:{areaColor:selectArea,borderColor:light?'#8C6226':'#eadac0',borderWidth:1}},data:values(),silent:dataPending.value||dataUnavailable.value}]})
    return
  }
  chart.setOption({
    animation:false,
    backgroundColor:'transparent',
    aria:{enabled:true,label:{description:description.value}},
    tooltip:tooltipOpt,
    visualMap:{show:false,min,max,calculable:false,inRange:{color:rangeColors}},
    series:[{
      id:SERIES_ID,name:'制作国家'+metricLabel.value+'分布',type:'map',map:MAP_NAME,nameProperty:'iso3',
      roam:true,scaleLimit:{min:1,max:6},...mapLayout,
      selectedMode:'single',silent:dataPending.value||dataUnavailable.value,
      label:{show:false},itemStyle:{areaColor:areaBase,borderColor:borderBase,borderWidth:.55},
      emphasis:{label:{show:false},itemStyle:{areaColor:hoverArea,borderColor:light?'#754F1B':'#e5d6fa',borderWidth:1}},
      select:{label:{show:false},itemStyle:{areaColor:selectArea,borderColor:light?'#8C6226':'#eadac0',borderWidth:1}},
      data:values()
    }]
  })
  drawn=true
}
function reset(){
  chart?.setOption({series:[{id:SERIES_ID,zoom:1,center:null,...mapLayout,data:values().map(row=>({...row,selected:false}))}]})
}
async function loadMap(){
  loading.value=true;error.value=''
  controller?.abort();controller=new AbortController()
  try{
    const response=await fetch('/m9/globe/world.geojson',{signal:controller.signal})
    if(!response.ok)throw new Error('世界边界资源暂时无法读取。')
    const source=await response.json() as BoundaryCollection
    if(source.type!=='FeatureCollection'||!Array.isArray(source.features))throw new Error('世界边界资料格式无效。')
    boundaries=source.features.filter(feature=>feature.properties?.iso3&&feature.properties.iso3!=='ATA')
    if(!boundaries.length)throw new Error('世界边界资料为空。')
    boundaryCodes.value=boundaries.map(feature=>feature.properties.iso3!)
    boundaryByIso2.value=Object.fromEntries(boundaries.filter(feature=>feature.properties.iso2).map(feature=>[feature.properties.iso2!,feature.properties.iso3!]))
    if(disposed||!mapHost.value)return
    registerMap(MAP_NAME,{type:'FeatureCollection',features:boundaries} as Parameters<typeof registerMap>[1])
    chart?.dispose();drawn=false
    chart=init(mapHost.value,undefined,{renderer:'svg'})
    chart.on('click',(params:{name?:string})=>{
      if(dataPending.value||dataUnavailable.value)return
      const feature=boundaries.find(item=>item.properties.iso3===params.name)
      if(feature?.properties.iso2)emit('select',feature.properties.iso2)
    })
    draw()
    observer?.disconnect();observer=new ResizeObserver(()=>chart?.resize());observer.observe(mapHost.value)
  }catch(reason){
    if(!disposed&&(reason as Error).name!=='AbortError')error.value=(reason as Error).message||'世界地图暂不可用。'
  }finally{if(!disposed)loading.value=false}
}
watch([rows,state,()=>props.metric,siteTheme],draw)
onMounted(loadMap)
onBeforeUnmount(()=>{disposed=true;controller?.abort();observer?.disconnect();chart?.dispose()})
defineExpose({reset})
</script>

<template>
  <div class="dashboard-world" :aria-busy="loading||dataPending">
    <div class="world-stage">
      <div ref="mapHost" class="world-canvas" role="img" :aria-label="description"/>
      <button v-if="!loading&&!error" class="map-reset" type="button" aria-label="复位世界地图视角" @click="reset">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 10a8 8 0 1 1 1 7M4 4v6h6"/></svg>复位
      </button>
      <div v-if="loading||dataPending" class="map-message" role="status">{{loading?'正在载入世界地图…':'正在读取国家统计…'}}</div>
      <div v-else-if="error" class="map-message map-error" role="alert"><p>{{error}}</p><button type="button" @click="loadMap">重新载入地图</button></div>
      <div v-else-if="dataUnavailable" class="map-message map-error" role="status">制作国家统计暂不可用。</div>
    </div>
    <div v-if="!error&&!loading&&!dataPending&&!dataUnavailable" class="map-caption">
      <span class="map-legend" :aria-label="'颜色越浅代表'+metricLabel+'越高'"><span>{{legendMin}}</span><i aria-hidden="true"/><span>{{legendMax}}</span></span>
      <span>{{rows.some(row=>row.value>0)?'拖动平移 · 滚轮缩放 · 点击国家查看速览':'暂无可定位的国家资料'}}</span>
    </div>
    <p v-if="unmappedCount&&!error&&!loading" class="map-unmapped">另有 {{unmappedCount}} 个国家或地区暂无边界轮廓，仍计入统计。</p>
    <div class="map-data-table"><table><caption>制作国家{{metricLabel}}，合拍影片可计入多个国家</caption><thead><tr><th>国家或地区</th><th>{{metricLabel}}</th></tr></thead><tbody><tr v-for="row in rows" :key="row.iso3"><td>{{row.name}}</td><td>{{metric==='count'?row.value:metric==='rating'?row.value.toFixed(1)+' 分 ('+row.samples+' 样本)':(row.value*100).toFixed(1)+'%'}}</td></tr></tbody></table></div>
  </div>
</template>

<style scoped>
.dashboard-world{display:flex;flex-direction:column;position:relative;min-width:0;min-height:250px;width:100%;height:100%;color:var(--mi-text-soft)}
.world-stage{position:relative;flex:1;min-height:160px}
.world-canvas{position:absolute;inset:0}
.map-reset{position:absolute;right:0;top:0;display:flex;align-items:center;gap:5px;border:1px solid var(--mi-border-strong);border-radius:7px;padding:6px 8px;background:var(--mi-surface);color:var(--mi-text-soft);font:inherit;font-size:11px;cursor:pointer;box-shadow:var(--mi-shadow-sm)}
.map-reset:hover{background:var(--mi-surface-hover);border-color:var(--mi-accent);color:var(--mi-text)}
.map-reset svg{height:14px;width:14px}
.map-reset:focus-visible,.map-message button:focus-visible{outline:2px solid var(--mi-accent);outline-offset:3px}
.map-message{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;background:var(--mi-bg);color:var(--mi-text-secondary);font-size:12px;text-align:center;padding:20px;line-height:1.7}
.map-message p{margin:0}
.map-error{color:var(--mi-negative)}
.map-message button{border:1px solid var(--mi-border-strong);border-radius:7px;padding:7px 12px;background:var(--mi-surface-raised);color:var(--mi-text);font:inherit;cursor:pointer}
.map-caption{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;padding-top:7px;color:var(--mi-text-muted);font-size:10px;line-height:1.5}
.map-legend{display:flex;align-items:center;gap:8px}
.map-legend i{display:block;width:82px;height:5px;background:linear-gradient(90deg,var(--mi-surface-raised),var(--mi-accent));border-radius:2px}
.map-unmapped{font-size:10px;line-height:1.6;color:var(--mi-text-muted);margin:7px 0 0}
.map-data-table{position:absolute;top:0;left:0;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media(prefers-reduced-motion:reduce){.dashboard-world *{transition:none!important}}
</style>
