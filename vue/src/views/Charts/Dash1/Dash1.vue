<template>
    <v-layout wrap>
<!--      <v-flex sm6 xs12 md6 lg3 v-for="item in panels" :key="item.name">-->
<!--        <v-card class="ma-3">-->
<!--          <v-list-item>-->
<!--            <v-list-item-avatar tile class="mt-n7">-->
<!--              <v-sheet :color="item.iconColor" width="100" height="100" elevation="10">-->
<!--                <v-icon dark large>{{item.icon}}</v-icon>-->
<!--              </v-sheet>-->
<!--            </v-list-item-avatar>-->
<!--            <v-list-item-content>-->
<!--              <div class="text-center text-h5">{{item.name}}</div>-->
<!--              <v-list-item-title class="headline mb-1 text-center">{{item.value}}</v-list-item-title>-->
<!--              <div><v-divider></v-divider></div>-->
<!--            </v-list-item-content>-->
<!--          </v-list-item>-->
<!--          <v-card-actions>-->
<!--            <v-icon text class="ma-2" color="green">alarms</v-icon>-->
<!--            <div>根据爬虫数据更新</div>-->
<!--          </v-card-actions>-->
<!--        </v-card>-->
<!--      </v-flex>-->
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="电影上映统计"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-base-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="优质电影的低潮高峰"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-sections-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex xs12>
        <v-basic-card
            title="各类型电影分析 （面积图）"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-area-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="科幻电影分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-rect-area-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="近年电影上映情况分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-split-area-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex xs12>
        <v-basic-card
            title="影片上映年份分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <line-big-data-chart />
          </template>
        </v-basic-card>
      </v-flex>
    </v-layout>
</template>

<script>
import LineBaseChart from "./charts/LineBaseChart";
import LineSectionsChart from './charts/LineSectionsChart.vue';
import LineAreaChart from './charts/LineAreaChart.vue';
import LineRectAreaChart from './charts/LineRectAreaChart.vue';
import LineSplitAreaChart from './charts/LineSplitAreaChart.vue';
import LineBigDataChart from './charts/LineBigDataChart.vue';

export default {
  name: "Dash1",
  components: {
    LineBaseChart,
    LineSectionsChart,
    LineAreaChart,
    LineRectAreaChart,
    LineSplitAreaChart,
    LineBigDataChart,
  },
  data: ()=>({
    panels: [{
      'iconColor': 'green',
      'icon': 'movie',
      'name': '电影数',
      'value': 252
    },{
      'iconColor': 'red',
      'icon': 'subscriptions',
      'name': '平均得分',
      'value': 8.89
    },{
      'iconColor': 'blue',
      'icon': 'star',
      'name': '评价数',
      'value': 158923537
    },{
      'iconColor': 'yellow',
      'icon': 'folder_shared',
      'name': '电影类型',
      'value': 27
    }],
  })
}
</script>

<style scoped>

</style>
