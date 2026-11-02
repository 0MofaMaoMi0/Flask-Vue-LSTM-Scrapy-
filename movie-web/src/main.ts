import {createApp} from 'vue'
import {createPinia} from 'pinia'
import {createRouter,createWebHistory} from 'vue-router'
import {createVuetify} from 'vuetify'
import App from './App.vue'
import './style.css'
import Welcome from './views/WelcomeView.vue'
import Login from './views/LoginView.vue'
import Register from './views/RegisterView.vue'
import Home from './views/HomeView.vue'
import {useAuthStore} from './stores/auth'
const pinia=createPinia()
const router=createRouter({history:createWebHistory(import.meta.env.BASE_URL),routes:[{path:'/',component:Welcome},{path:'/login',component:Login},{path:'/register',component:Register},{path:'/home',component:Home,meta:{requiresAuth:true}},{path:'/:pathMatch(.*)*',redirect:'/'}]})
router.beforeEach(async to=>{
 const auth=useAuthStore(pinia)
 if(!auth.ready){try{await auth.initialize()}catch{return to.meta.requiresAuth?'/login':true}}
 if(to.meta.requiresAuth&&!auth.user)return '/login'
 if(auth.user&&(to.path==='/login'||to.path==='/register'))return '/home'
 return true
})
router.afterEach(to=>{document.title=({'/':'MovieInsight','/login':'登录 · MovieInsight','/register':'注册 · MovieInsight','/home':'认证验证 · MovieInsight'} as Record<string,string>)[to.path]||'MovieInsight'})
createApp(App).use(pinia).use(router).use(createVuetify()).mount('#app')
