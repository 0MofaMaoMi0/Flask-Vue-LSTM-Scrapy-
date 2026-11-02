<script setup lang="ts">
import {reactive, ref, onMounted, onBeforeUnmount} from 'vue'
const orders = [[10,6,18,31],[4,13,2,28],[5,25,17,20],[11,0,27,14],[22,9,7,30],[16,3,26,19],[21,15,8,24],[1,23,29,12]]
const durations=[53,62,48,67,57,71,59,65], delays=[5,28,17,38,9,47,30,22]
const ready=reactive(new Set<number>()), missing=reactive(new Set<number>())
const grid=ref<HTMLElement|null>(null)
const repeats=ref(1)
let observer:ResizeObserver|undefined
onMounted(()=>{
 observer=new ResizeObserver(()=>{
  const strips=Array.from(grid.value?.querySelectorAll<HTMLElement>('.strip')??[]).filter(el=>el.clientWidth>0)
  // Fill with real tiles, never stretch a sequence with empty min-height space.
  repeats.value=Math.max(1,...strips.map(el=>Math.ceil(el.clientHeight/(el.clientWidth*1.35*4))))
 })
 if(grid.value)observer.observe(grid.value)
})
onBeforeUnmount(()=>observer?.disconnect())
const poster=(i:number)=>`${import.meta.env.BASE_URL}posters/${String(i+1).padStart(2,'0')}.jpg`
</script><template><div class="wall" aria-hidden="true"><div ref="grid" class="wall-grid"><div v-for="(order,col) in orders" :key="col" class="strip" :style="{'--duration':durations[col]+'s'}"><div class="track" :style="{animationDelay:'-'+delays[col]+'s'}"><div v-for="copy in 2" :key="copy" class="set"><div v-for="(i,index) in Array.from({length:repeats},()=>order).flat()" :key="index" class="poster" :class="{missing:missing.has(i)}"><img :src="poster(i)" alt="" width="342" height="513" decoding="async" :class="{ready:ready.has(i)}" @load="ready.add(i);missing.delete(i)" @error="missing.add(i);ready.delete(i)"></div></div></div></div></div><div class="shade"></div><div class="focus-vignette"></div><div class="film-edge"></div><div class="film-bars"></div></div></template>