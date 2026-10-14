import '@fortawesome/fontawesome-free/css/all.css'
import 'material-design-icons-iconfont/dist/material-design-icons.css'
import Vue from 'vue';
import Vuetify from 'vuetify/lib/framework';
import minifyTheme from "minify-css-string";

//icons
import predict from "../components/icons/Predict.vue";
import home from "../components/icons/Home.vue";
import spotify from "../components/icons/Spotify.vue";
import menu from "../components/icons/Menu.vue";
import info from "../components/icons/Info.vue";
import settings from "../components/icons/Settings.vue";
import library from "../components/icons/Library.vue";
import trending from "../components/icons/Trending.vue";
import listen from "../components/icons/Listen.vue";
import dotsVertical from "../components/icons/DotsVertical.vue";
import music from "../components/icons/Music.vue";
import search from "../components/icons/Search.vue";
import microphone from "../components/icons/Microphone.vue";
import dotchart from "../components/icons/DotChart.vue";
import timeline from "../components/icons/timeline.vue";

import '@mdi/font/css/materialdesignicons.css'
Vue.use(Vuetify);

export default new Vuetify({
  icons: {
    values:{
      home: { component: home },
      spotify: { component: spotify },
      menu: { component: menu },
      info: { component: info },
      settings: { component: settings },
      library: { component: library },
      trending: { component: trending },
      listen: { component: listen },
      "dots-vertical": { component: dotsVertical },
      music: { component: music },
      search: { component: search },
      microphone: { component: microphone },
      timeline: { component: timeline },
      dotchart: { component: dotchart },
      predict: { component: predict },
    },
    iconfont: 'fa' || 'md',
  },
  theme: {
    options: {
      minifyTheme,
      themeCache: {
        get: (key) => localStorage.getItem(key),
        set: (key, value) => localStorage.setItem(key, value),
      },
    },
    themes: {
      dark: {
        background: "#000000",
        primary: "#4edf93",
        secondary: "#67a4ff",
        accent: "#7673fe",
        error: "#ff5474",
        info: "#3251f8",
        success: "#4CAF50",
        warning: "#ffa133",
      },
    },
    dark: true,
  },
});

