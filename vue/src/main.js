import Vue from 'vue'
import App from './App.vue'
import router from './router'
import store from './store'
import vuetify from './plugins/vuetify'
import './plugins/echarts';

// import mixins
import "./mixins/global";
import './icons'//icon
import VBasicCard from './components/VBasicCard.vue';
Vue.component('v-basic-card', VBasicCard);

Vue.config.productionTip = false

import snackbar from 'vuetify-snackbars'
Vue.use(snackbar)

new Vue({
  router,
  store,
  vuetify,
  render: h => h(App)
}).$mount('#app')
