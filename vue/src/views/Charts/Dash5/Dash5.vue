<template>
  <v-container grid-list-lg pa-0>
    <v-layout wrap>
      <v-flex xs12>
        <v-basic-card
            title="电影国家分析"
            toolbar-height="56">
          <template slot="card-content">
            <global-map/>
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex xs12>
        <v-basic-card
            title="电影时间轴分析"
            toolbar-height="56">
          <template slot="card-content">
            <time-chart/>
          </template>
        </v-basic-card>
      </v-flex>
    </v-layout>
  </v-container>
</template>


<script>
import GlobalMap from './GlobalMap'
import TimeChart from "./TimeChart";

export default {
  name: 'VBarIndex',
  components: {
    GlobalMap,TimeChart
  },
};
</script>
