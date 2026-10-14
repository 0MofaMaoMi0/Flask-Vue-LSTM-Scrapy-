<template>
  <v-app id="app">
    <v-app-bar color="transparent" elevation="0" app class="py-4">
      <v-icon class="mb-6" @click.stop="drawerMenuClick" v-if="screenIsSmall"
      >$menu</v-icon
      >
      <v-row justify="space-between" v-if="!screenIsSmall">
        <v-toolbar-title class="text-uppercase ">
<!--          <span class="font-weight-light">AAE</span>-->
<!--          <span>IdeaPro</span>-->
        </v-toolbar-title>
        <v-col md="2" cols="12">
          <v-badge
              bottom
              color="success"
              overlap
              offset-x="12"
              offset-y="12"
              class="ms-4"
              dot
          >
            <v-avatar
                size="40px"
            >
              <v-img :src="avatar"></v-img>
            </v-avatar>
          </v-badge>

          <v-btn text @click="logout">
            <span>退出</span>
            <v-icon right>exit_to_app</v-icon>
          </v-btn>
<!--          搜索框  <search-box />-->
        </v-col>
      </v-row>
    </v-app-bar>
    <v-navigation-drawer
        width="230"
        v-model="drawer"
        color="black"
        app
        :mini-variant="miniDrawer"
    >
      <v-list>
        <v-list-item>
          <v-list-item-avatar>
            <v-icon size="35" v-if="!miniDrawer">$spotify</v-icon>
<!--            <svg-icon  v-if="!miniDrawer" :icon-class="appIcon" color="green"></svg-icon>-->
            <v-icon size="20" class="mr-2" v-else @click.stop="drawerMenuClick"
            >$menu</v-icon
            >
          </v-list-item-avatar>
          <v-list-item-title class="font-black">{{appName}}</v-list-item-title>
          <v-list-item-action>
            <v-icon @click.stop="drawerMenuClick">$menu</v-icon>
          </v-list-item-action>
        </v-list-item>
      </v-list>
      <v-divider inset class="mx-auto"></v-divider>
      <v-list nav color="transparent" flat class="mt-3">
        <v-list-item
            v-for="(navItem, index) in navItems"
            :key="index"
            class="pr-6"
            link
            exact
            :to="navItem.link"
            exact-active-class="active-nav-link"
        >
          <v-tooltip nudge-right="8" right :z-index="miniDrawer ? 900000 : -5">
            <template v-slot:activator="{ on, attrs }">
              <v-icon v-bind="attrs" v-on="on" class="mr-6">{{
                  navItem.icon
                }}</v-icon>
<!--              <svg-icon v-bind="attrs" :icon-class="navItem.icon" v-on="on" class="mr-6"/>-->
              <span>{{ navItem.title }}</span>
            </template>
            <span>{{ navItem.title }}</span>
          </v-tooltip>
        </v-list-item>
      </v-list>
    </v-navigation-drawer>
    <v-main class="mb-16 pb-16 mx-md-10 mx-3">
      <v-container fluid>
        <router-view />
      </v-container>
    </v-main>
  </v-app>
</template>

<script>
import mixin from "../../mixins/mixins";

export default {
  name: "AppLayout",
  mixins: [mixin],
  data: ()=>({
    drawer: true,
    miniDrawer: false,
    navItems: [
      {
        title: "主页",
        icon: "$home",
        link: "/home",
      },
      {
        title: "电影库",
        icon: "$library",
        link: "/library",
      },
      {
        title: "数据分析",
        icon: "$listen",
        link: "/dash1",
      },
      {
        title: "数据统计",
        icon: "$trending",
        link: "/dash2",
      },
      {
        title: "词云分析",
        icon: "$music",
        link: "/dash3",
      },
      {
        title: "评分分析",
        icon: "$dotchart",
        link: "/dash4",
      },
      {
        title: "时空分析",
        icon: "$timeline",
        link: "/dash5",
      },
      // {
      //   title: "设置",
      //   icon: "$settings",
      //   link: "/settings",
      // },
      // {
      //   title: "情感预测",
      //   icon: "$predict",
      //   link: "/predict",
      // },
      {
        title: "信息设置",
        icon: "$settings",
        link: "/settings",
      },
    ],
  }),
  methods: {
    drawerMenuClick() {
      if (this.screenIsSmall) {
        this.miniDrawer = false;
        this.drawer = !this.drawer;
      } else {
        this.miniDrawer = !this.miniDrawer;
      }
    },
    logout() {
      console.log('注销')
      localStorage.removeItem('uid')
      this.$router.push('/')
    }
  },
}
</script>

<style>
svg {
  fill: currentColor;
}
path {
  fill: currentColor;
}
#app {
  overflow-x: hidden;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-align: center;
}
.active-nav-link {
  color: #1ed760 !important;
}
.d-hidden {
  opacity: 0;
}
</style>
