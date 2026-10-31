<template>
    <div class="about">
      <h1>团队介绍</h1>
      <h2 class="mt-4"> <svg-icon color="lightgreen"  class="mr-3" :icon-class="appIcon" />{{appName}}大数据这款作品，这个是我们团队呕心沥血投入巨大精力开发的作品，希望能够带来好的市场反应</h2>
      <v-container class="mt-8">
        <v-card outlined class="mb-6 pa-4">
          <v-card-title>数据与图片来源说明</v-card-title>
          <v-card-text>
            <p>
              部分电影海报可通过
              <a href="https://www.themoviedb.org" target="_blank" rel="noopener noreferrer">TMDB</a>
              API 获取；启用该来源时请同时遵守 TMDB 的许可、署名和品牌规范。
            </p>
            <p class="mb-0">
              This product uses the TMDB API but is not endorsed or certified by TMDB.
            </p>
          </v-card-text>
        </v-card>
        <v-layout row wrap>
          <v-flex style="display: none;" sm6 xs12 md6 lg3 v-for="item in panels" :key="item.name">
            <v-card class="ma-3">
              <v-list-item>
                <v-list-item-avatar tile class="mt-n7">
                  <v-sheet :color="item.iconColor" width="100" height="100" elevation="10">
                    <v-icon dark large>{{item.icon}}</v-icon>
                  </v-sheet>
                </v-list-item-avatar>
                <v-list-item-content>
                  <div class="text-center text-h5">{{item.name}}</div>
                  <v-list-item-title class="headline mb-1 text-center">{{item.value}}</v-list-item-title>
                  <div><v-divider></v-divider></div>
                </v-list-item-content>
              </v-list-item>
              <v-card-actions>
                <v-icon text class="ma-2" color="green">alarms</v-icon>
                <div>根据爬虫数据更新</div>
              </v-card-actions>
            </v-card>
          </v-flex>
          <v-flex xs12 sm6 md4 lg3 v-for="person in team" :key="person.name">
            <v-card class="text-center ma-3">
              <v-responsive class="pt-4">
                <v-avatar size="100" class="red lighten-2">
                  <img :src="person.avatar" alt="">
                </v-avatar>
              </v-responsive>
              <v-card-text>
                <div class="subheading">{{ person.name }}</div>
                <div class="grey--text">{{ person.role }}</div>
              </v-card-text>
              <v-card-actions>
                <v-btn outlined color="orange" @click="connect(person.name)">
                  <v-icon small left>message</v-icon>
                  <span>联系他</span>
                </v-btn>
              </v-card-actions>
            </v-card>
          </v-flex>
        </v-layout>
      </v-container>
    </div>
</template>

<script>
import mixin from '../mixins/mixins'

export default {
  name: 'team',
  components: {},
  mixins: [mixin],
  data: () => ({
    panels: [{
      'iconColor': 'green',
      'icon': 'movie',
      'name': '电影数',
      'value': 252
    },{
      'iconColor': 'red',
      'icon': 'subscriptions',
      'name': '平均得分',
      'value': 8.4
    },{
      'iconColor': 'blue',
      'icon': 'star',
      'name': '评价数',
      'value': 12225413
    },{
      'iconColor': 'yellow',
      'icon': 'folder_shared',
      'name': '电影类型',
      'value': 23
    }],
    team: [
      {name: '小王（组长）', role: 'Flask 开发', avatar: '/img1.png'},
      {name: '小美', role: '前端设计', avatar: '/img2.png'},
      {name: '小菊', role: '数据库设计', avatar: '/img3.png'},
      {name: '老王', role: '资料整理', avatar: '/img4.png'},
    ]
  }),
  methods: {
    connect(p){
      this.$snackbar({content: p + 'QQ:81040295  ...请勿随意骚扰', outlined: true, top:true, center:true, color:'pink',multiLine: true})
    }
  }
}
</script>
