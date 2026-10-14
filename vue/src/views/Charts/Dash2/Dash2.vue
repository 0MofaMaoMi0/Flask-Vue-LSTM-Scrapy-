<template>
  <v-layout wrap>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="国家/地区发片量排行分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <bar-base-chart />
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="国家/地区发片量构成分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <base-pie-chart/>
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="电影类型发片量排行分析"
            toolbar-height="56"
        >
          <template slot="card-content">
           <bar-base-chart2/>
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="电影类型发片量构成分析"
            toolbar-height="56"
        >
          <template slot="card-content">
            <base-pie-chart2/>
          </template>
        </v-basic-card>
      </v-flex>
      <v-flex
          xs12
          lg6
      >
        <v-basic-card
            title="好评率"
            toolbar-height="56"
        >
          <template slot="card-content">
            <liquid-fill-chart/>
          </template>
        </v-basic-card>
      </v-flex>
    </v-layout>
</template>


<script>
import BarBaseChart from './charts/BarBaseChart.vue';
import BarBaseChart2 from './charts/BarBaseChart2.vue';

// import BarPlotChart from './charts/BarPlotChart.vue';
import BasePieChart from "./charts/BasePieChart";
import BasePieChart2 from "./charts/BasePieChart2";

// import BarWaterfallChart from './charts/BarWaterfallChart.vue';
// import BarShadowChart from './charts/BarShadowChart.vue';
import LiquidFillChart from "./charts/LiquidFillChart";

export default {
  name: 'VBarIndex',
  components: {
    BarBaseChart,
    BasePieChart,BasePieChart2,
    BarBaseChart2,LiquidFillChart
  },
};
</script>
