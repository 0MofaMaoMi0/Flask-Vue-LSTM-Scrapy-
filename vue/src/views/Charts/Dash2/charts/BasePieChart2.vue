<template>
  <v-chart
    style="width:100%;"
    :options="chartOption"
    autoresize
  />
</template>

<script>
import {getTypeRank} from "../../../../api/movie";
export default {
  name: 'WaterfallBarChart',
  data() {
    return {
      chartOption: {},
      d1: [],
      l1: []
    };
  },
  async mounted() {
    await getTypeRank().then(res => {
      this.d1 = res.data.data.datas
      this.l1 = res.data.data.datas.map(i => i.name)
    })
    this.chartOption = this.buildChartOption();
    // this.startInterval();
  },
  methods: {
    buildChartOption() {
      const option = {
        tooltip: {
          trigger: 'item',
          formatter: '{a} <br/>{b} : {c} ({d}%)'
        },
        legend: {
          type: 'scroll',
          orient: 'vertical',
          right: 10,
          top: 20,
          bottom: 20,
          data: this.l1,
          textStyle: {
            color: '#F1F1F3'
          }
        },
        series: [
          {
            name: '电影数量',
            type: 'pie',
            radius: ['40%', '70%'],
            center: ['40%', '50%'],
            data: this.d1,
            avoidLabelOverlap: false,
            emphasis: {
              itemStyle: {
                shadowBlur: 10,
                shadowOffsetX: 0,
                shadowColor: 'rgba(0, 0, 0, 0.5)'
              }
            }
          }
        ]
      };

      return option;
    },
  },
};
</script>
