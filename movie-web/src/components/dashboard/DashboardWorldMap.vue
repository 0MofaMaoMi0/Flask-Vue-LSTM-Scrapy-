<script setup lang="ts">
import {computed,onBeforeUnmount,onMounted,ref,watch} from 'vue'
import {init,registerMap,use,type EChartsType} from 'echarts/core'
import {MapChart} from 'echarts/charts'
import {AriaComponent,TooltipComponent,VisualMapComponent} from 'echarts/components'
import {SVGRenderer} from 'echarts/renderers'
import {ISO2_TO_ISO3} from '../../lib/globe/iso2to3.js'
import {countryName} from '../../lib/globe/countryDisplay.js'

use([MapChart,TooltipComponent,VisualMapComponent,AriaComponent,SVGRenderer])

type CountryRow={iso2:string;count:number;name?:string}
type BoundaryFeature={type:'Feature';properties:{iso2?:string;iso3?:string;name?:string};geometry:unknown}
type BoundaryCollection={type:'FeatureCollection';features:BoundaryFeature[]}
const props=withDefaults(defineProps<{countries:CountryRow[];status?:string}>(),{status:'ready'})
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
  const unique=new Map<string,{iso2:string;iso3:string;name:string;count:number}>()
  for(const row of props.countries){
    const iso2=typeof row.iso2==='string'?row.iso2.trim().toUpperCase():''
    const iso3=boundaryByIso2.value[iso2]||COUNTRY_CODES[iso2]
    if(!iso3||!Number.isFinite(row.count)||row.count<0)continue
    unique.set(iso3,{iso2,iso3,name:countryName(COUNTRY_CODES[iso2]||iso3,row.name||iso2),count:Math.floor(row.count)})
  }
  return [...unique.values()].sort((a,b)=>b.count-a.count||a.iso3.localeCompare(b.iso3))
})
const maxCount=computed(()=>Math.max(0,...rows.value.map(row=>row.count)))
const description=computed(()=>dataUnavailable.value?'制作国家统计暂不可用。':dataPending.value?'正在读取制作国家统计。':rows.value.some(row=>row.count>0)?`世界电影分布，${rows.value.filter(row=>row.count>0).length} 个制作国家或地区有影片。合拍影片可计入多个国家，统计按国家内电影 ID 去重。`:'当前没有可定位的制作国家资料。')
const unmappedCount=computed(()=>{
  const supported=new Set(boundaryCodes.value)
  return rows.value.filter(row=>row.count>0&&!supported.has(row.iso3)).length
})

function values(){
  const counts=new Map(rows.value.map(row=>[row.iso3,row.count]))
  return boundaries.map(feature=>({name:feature.properties.iso3!,value:counts.get(feature.properties.iso3!)||0}))
}
function tooltip(params:{name?:string}){
  if(dataPending.value||dataUnavailable.value)return description.value
  const iso3=params.name||''
  const row=rows.value.find(item=>item.iso3===iso3)
  const properties=boundaries.find(feature=>feature.properties.iso3===iso3)?.properties
  const name=row?.name||countryName(COUNTRY_CODES[properties?.iso2||'']||iso3,properties?.name||iso3)
  return `${name}\n${(row?.count||0).toLocaleString('zh-CN')} 部影片`
}
function draw(){
  if(!chart)return
  const max=Math.max(1,maxCount.value)
  if(drawn){
    chart.setOption({aria:{label:{description:description.value}},visualMap:{max},series:[{id:SERIES_ID,data:values(),silent:dataPending.value||dataUnavailable.value}]})
    return
  }
  chart.setOption({
    animation:false,
    backgroundColor:'transparent',
    aria:{enabled:true,label:{description:description.value}},
    tooltip:{trigger:'item',renderMode:'richText',confine:true,formatter:tooltip,backgroundColor:'#1c192b',borderColor:'#6d5a8c',borderWidth:1,padding:[9,12],textStyle:{color:'#f2edf9',fontSize:12,lineHeight:20}},
    visualMap:{show:false,min:0,max,calculable:false,inRange:{color:['#292235','#4b3967','#77569e','#b9a0f4']}},
    series:[{
      id:SERIES_ID,name:'制作国家影片分布',type:'map',map:MAP_NAME,nameProperty:'iso3',
      roam:true,scaleLimit:{min:1,max:6},...mapLayout,
      selectedMode:'single',silent:dataPending.value||dataUnavailable.value,
      label:{show:false},itemStyle:{areaColor:'#292235',borderColor:'#756587',borderWidth:.55},
      emphasis:{label:{show:false},itemStyle:{areaColor:'#c5abea',borderColor:'#e5d6fa',borderWidth:1}},
      select:{label:{show:false},itemStyle:{areaColor:'#d3bb91',borderColor:'#eadac0',borderWidth:1}},
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
watch([rows,state],draw)
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
      <span class="map-legend" aria-label="颜色越浅代表影片越多"><span>0</span><i aria-hidden="true"/><span>{{maxCount.toLocaleString('zh-CN')}} 部</span></span>
      <span>{{rows.some(row=>row.count>0)?'拖动平移 · 滚轮缩放':'暂无可定位的国家资料'}}</span>
    </div>
    <p v-if="unmappedCount&&!error&&!loading" class="map-unmapped">另有 {{unmappedCount}} 个国家或地区暂无边界轮廓，仍计入统计。</p>
    <div class="map-data-table"><table><caption>制作国家影片数，合拍影片可计入多个国家</caption><thead><tr><th>国家或地区</th><th>影片数</th></tr></thead><tbody><tr v-for="row in rows" :key="row.iso3"><td>{{row.name}}</td><td>{{row.count}}</td></tr></tbody></table></div>
  </div>
</template>

<style scoped>
.dashboard-world{display:flex;flex-direction:column;position:relative;min-width:0;min-height:250px;width:100%;height:100%;color:#d8cde8}
.world-stage{position:relative;flex:1;min-height:160px}
.world-canvas{position:absolute;inset:0}
.map-reset{position:absolute;right:0;top:0;display:flex;align-items:center;gap:5px;border:1px solid #66567b;border-radius:7px;padding:6px 8px;background:#171321;color:#ddd1f0;font:inherit;font-size:11px;cursor:pointer}
.map-reset:hover{background:#2b203d;border-color:#a48ac3;color:#fff}
.map-reset svg{height:14px;width:14px}
.map-reset:focus-visible,.map-message button:focus-visible{outline:2px solid #d0b7f6;outline-offset:3px}
.map-message{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:12px;background:#0d0b15d9;color:#c5b5da;font-size:12px;text-align:center;padding:20px;line-height:1.7}
.map-message p{margin:0}
.map-error{color:#e4bbce}
.map-message button{border:1px solid #8f74b7;border-radius:7px;padding:7px 12px;background:#2a2039;color:#eee2ff;font:inherit;cursor:pointer}
.map-caption{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;padding-top:7px;color:#b5a6c8;font-size:10px;line-height:1.5}
.map-legend{display:flex;align-items:center;gap:8px}
.map-legend i{display:block;width:82px;height:5px;background:linear-gradient(90deg,#292235,#4b3967,#77569e,#b9a0f4);border-radius:2px}
.map-unmapped{font-size:10px;line-height:1.6;color:#b5a6c8;margin:7px 0 0}
.map-data-table{position:absolute;top:0;left:0;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
@media(prefers-reduced-motion:reduce){.dashboard-world *{transition:none!important}}
</style>
