import {defineConfig,loadEnv} from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig(({mode})=>({plugins:[vue()],server:{host:'127.0.0.1',port:5174,strictPort:true,proxy:{'/api/v1':{target:loadEnv(mode,'.','MI2_').MI2_API_TARGET||'http://127.0.0.1:8091',changeOrigin:false}}}}))
