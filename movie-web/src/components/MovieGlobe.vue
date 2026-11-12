<script setup>
import {onMounted,onBeforeUnmount,ref,watch} from 'vue';
import {createMovieGlobe} from '../lib/globe/globeEngine.js';
const props=defineProps({stats:{type:Object,required:true},metric:{type:String,required:true},selectedIso3:{type:String,default:''},theme:{type:String,default:'cinema'},effects:{type:String,default:'vivid'},initialView:{type:Object,default:()=>({lat:18,lng:-35})},labelIso3:{type:Array,default:()=>[]},autoRotate:{type:Boolean,default:true}});
const emit=defineEmits(['hover','select']);
const mountEl=ref(null);
const error=ref('');
const status=ref('loading');
let engine=null;
let destroyed=false;
let features=[];
let recentStats=props.stats,recentMetric=props.metric;
const isReady=()=>status.value==='ready';
function focus(iso3){engine?.selectCountry(iso3);}
function reset(){engine?.reset();}
function setAutoRotate(value){engine?.setAutoRotate(value);}
defineExpose({focus,reset,setAutoRotate,isReady});
onMounted(async()=>{
  try{
    const [{default:Globe}, response]=await Promise.all([import('globe.gl'),fetch('/m9/globe/world.geojson')]);
    if(!response.ok)throw new Error('国家边界数据读取失败 ('+response.status+')');
    const geo=await response.json();
    features=geo.features.filter(f=>f.properties.iso3!=='ATA');
    if(destroyed)return;
    engine=createMovieGlobe({element:mountEl.value,GlobeConstructor:Globe,features,
      getStats:()=>recentStats,getMetric:()=>recentMetric,getSelected:()=>props.selectedIso3,
      themeId:props.theme,effects:props.effects,initialView:props.initialView,labelIso3:props.labelIso3,initialAutoRotate:props.autoRotate,
      onHover:iso3=>emit('hover',iso3),onSelect:iso3=>emit('select',iso3)});
    status.value='ready';
  }catch(e){error.value=e?.message||String(e);status.value='error';}
});
watch(()=>props.stats,stat=>{recentStats=stat;engine?.refresh();});
watch([()=>props.theme,()=>props.effects],()=>engine?.setVisuals({themeId:props.theme,effects:props.effects}));
watch(()=>props.labelIso3,codes=>engine?.setLabelIso3(codes),{deep:true});
watch(()=>props.autoRotate,value=>engine?.setAutoRotate(value));
watch(()=>props.metric,metric=>{recentMetric=metric;engine?.refresh();});
watch(()=>props.selectedIso3,code=>{
  if(!engine)return;
  if(code)engine.selectCountry(code);
  else engine.reset();
});
onBeforeUnmount(()=>{destroyed=true;engine?.dispose();engine=null;});
</script>
<template>
  <div class="globe-viewport">
    <div ref="mountEl" class="globe-canvas" aria-label="可旋转三维世界地图，点击国家查看观影统计"/>
    <div v-if="status==='loading'" class="globe-overlay">正在初始化 Globe.GL 真实 WebGL 引擎…</div>
    <div v-if="status==='error'" class="globe-overlay error"><strong>无法初始化三维地球</strong><p>{{error}}</p><small>浏览器可能不支持 WebGL，或地球资源加载失败；可刷新重试。</small></div>
  </div>
</template>
