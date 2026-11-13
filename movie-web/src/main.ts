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
import AIExplore from './views/AIExploreView.vue'
const Insights=()=>import('./views/InsightsView.vue')
import Catalog from './views/LibraryView.vue'
const Admin=()=>import('./views/AdminView.vue')
import {useAuthStore} from './stores/auth'
import {accountSettingsTarget,isAccountSettingsTab} from './services/accountSettings'
const pinia=createPinia()
const router=createRouter({history:createWebHistory(import.meta.env.BASE_URL),scrollBehavior(to,from,saved){return saved||{top:0}},routes:[{path:'/settings',component:()=>import('./views/SettingsView.vue'),meta:{requiresAuth:true}},{path:'/recommendations',component:()=>import('./views/RecommendationView.vue'),meta:{requiresAuth:true}},{path:'/ai',component:AIExplore,meta:{requiresAuth:true}},{path:'/insights',component:Insights,meta:{requiresAuth:true}},{path:'/movies',component:Catalog,props:{authenticated:true},meta:{requiresAuth:true}},{path:'/admin',component:Admin,meta:{requiresAuth:true,admin:true}},{path:'/',component:Welcome},{path:'/login',component:Login},{path:'/register',component:Register},{path:'/home',component:Home,meta:{requiresAuth:true}},{path:'/:pathMatch(.*)*',redirect:'/'}]})
router.beforeEach(async to=>{
 const auth=useAuthStore(pinia)
 if(!auth.ready){try{await auth.initialize()}catch{return to.meta.requiresAuth?'/login':true}}
 if(to.meta.requiresAuth&&!auth.user)return '/login'
 if(to.meta.admin&&!auth.user?.is_admin)return '/home'
 if(to.path==='/ai'&&isAccountSettingsTab(to.query.settings))return {...accountSettingsTarget(to.query.settings,'/ai'),replace:true}
 if(to.path==='/movies'&&to.query.assistant==='1')return '/ai'
 if(auth.user&&(to.path==='/login'||to.path==='/register'))return '/home'
 return true
})
router.afterEach(to=>{
 const titles={'/':'MovieInsight','/login':'登录 · MovieInsight','/register':'注册 · MovieInsight','/home':'影院首页 · MovieInsight','/ai':'AI 探索 · MovieInsight','/settings':'全局设置 · MovieInsight','/movies':'电影库 · MovieInsight','/admin':'数据驾驶舱 · MovieInsight','/insights':'数据洞察 · MovieInsight'} as Record<string,string>
 document.title=to.path==='/movies'&&to.hash==='#behavior'?'我的电影 · MovieInsight':titles[to.path]||'MovieInsight'
})
createApp(App).use(pinia).use(router).use(createVuetify()).mount('#app')


