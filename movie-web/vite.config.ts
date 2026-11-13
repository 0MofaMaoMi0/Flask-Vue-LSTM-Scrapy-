import {defineConfig,loadEnv} from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({mode})=>{
  const target=loadEnv(mode,'.','MI2_').MI2_API_TARGET||'http://127.0.0.1:8091'
  return {
    plugins:[vue()],
    // Keep the live app cache separate from temporary SSR/test Vite instances.
    cacheDir:'../.local-cache/movie-web-vite',
    optimizeDeps:{include:['echarts/core','echarts/charts','echarts/components','echarts/renderers']},
    server:{host:'127.0.0.1',port:5174,strictPort:true,watch:{ignored:['**/.*.tmpdir/**','**/*.tmpdir/**']},proxy:{'/api/catalog':{target,changeOrigin:false},'/api/admin':{target,changeOrigin:false},'/api/media':{target,changeOrigin:false},'/api/v1':{target,changeOrigin:false}}},
    build:{chunkSizeWarningLimit:1400,rollupOptions:{output:{manualChunks(id){
      if(id.includes('/three/'))return 'three'
      if(id.includes('globe.gl'))return 'globe'
      if(id.includes('echarts')||id.includes('zrender'))return 'charts'
      if(id.includes('node_modules'))return 'vendor'
    }}}}
  }
})
