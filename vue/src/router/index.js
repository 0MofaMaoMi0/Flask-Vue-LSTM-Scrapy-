import Vue from 'vue'
import VueRouter from 'vue-router'
import Login from '../views/Login.vue'
import Layout from "../views/layouts/AppLayout";
// import Home from "../views/Home";

Vue.use(VueRouter)

const routes = [
  {
    path: '/',
    name: 'Login',
    component: Login
  },
  {
    path: '/about',
    name: 'About',
    component: Layout,
    children: [
      {
        path: '/about',
        name: 'About2',
        component: () => import('../views/About.vue'),
      }
    ],
  },
  {
    path: '/index',
    name: 'Index',
    component: Layout,
    redirect: { name: 'Home' },
    children: [
      {
        path: '/home',
        name: 'Home',
        component: () => import('../views/Home.vue'),
      }
    ],
  },
  {
    path: '/library',
    name: 'Library',
    component: Layout,
    redirect: { name: 'LibraryC' },
    children: [
      {
        path: '/library',
        name: 'LibraryC',
        component: () => import('../views/Library.vue'),
      }
    ],
  },
  {
    path: '/dash1',
    name: 'Dash1',
    component: Layout,
    redirect: { name: 'Dash1V' },
    children: [
      {
        path: '/dash1',
        name: 'Dash1V',
        component: () => import('../views/Charts/Dash1/Dash1.vue'),
      }
    ],
  },
  {
    path: '/dash2',
    name: 'Dash2',
    component: Layout,
    redirect: { name: 'Dash2V' },
    children: [
      {
        path: '/dash2',
        name: 'Dash2V',
        component: () => import('../views/Charts/Dash2/Dash2.vue'),
      }
    ],
  },
  {
    path: '/dash3',
    name: 'Dash3',
    component: Layout,
    redirect: { name: 'Dash3V' },
    children: [
      {
        path: '/dash3',
        name: 'Dash3V',
        component: () => import('../views/Charts/Dash3/Dash3.vue'),
      }
    ],
  },
  {
    path: '/dash4',
    name: 'Dash4',
    component: Layout,
    redirect: { name: 'Dash4V' },
    children: [
      {
        path: '/dash4',
        name: 'Dash4V',
        component: () => import('../views/Charts/Dash4/Dash4.vue'),
      }
    ],
  },
  {
    path: '/dash5',
    name: 'Dash5',
    component: Layout,
    redirect: { name: 'Dash5V' },
    children: [
      {
        path: '/dash5',
        name: 'Dash5V',
        component: () => import('../views/Charts/Dash5/Dash5.vue'),
      }
    ],
  },
  {
    path: '/settings',
    name: 'Setting',
    component: Layout,
    redirect: { name: 'AccountSettings' },
    children: [
      {
        path: '/settings',
        name: 'AccountSettings',
        component: () => import('../views/Pages/AccountSettings.vue'),
      }
    ],
  },
  {
    path: '/predict',
    name: 'Predict',
    component: Layout,
    redirect: { name: 'MachineLearning' },
    children: [
      {
        path: '/predict',
        name: 'MLMain',
        component: () => import('../views/MachineLearning/MLMain.vue'),
      }
    ],
  },
]

const router = new VueRouter({
  mode: 'history',
  base: process.env.BASE_URL,
  routes
})

export default router
