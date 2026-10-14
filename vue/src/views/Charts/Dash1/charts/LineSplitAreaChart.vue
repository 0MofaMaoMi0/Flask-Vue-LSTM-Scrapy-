<template>
  <v-chart
    style="width:100%;"
    :options="chartOption"
    autoresize
  />
</template>

<script>
import {getChart3} from "../../../../api/movie";

export default {
  name: 'SplitAreaLineChart',
  data() {
    return {
      chartOption: {},
      xAxisData: [],
      seriesAData: [],
    };
  },
  async mounted() {
    await getChart3().then(res => {
      this.xAxisData = res.data.data.map(i => i.name)
      this.seriesAData = res.data.data.map(i => i.value)
    })

    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      // console.log('xAxisData',xAxisData);
      const option = {
        xAxis: {
          type: 'category',
          data: this.xAxisData,
          axisLine: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        legend: {
          icon: 'rect',
          itemGap: 15,
          itemWidth: 16,
          itemHeight: 10,
          data: ['上映数'],
          textStyle: {
            fontSize: 14,
            color: '#ECEFF1',
          },
        },
        yAxis: {
          type: 'value',
          axisLine: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        series: [
          {
            name: '上映数',
            data: this.seriesAData,
            type: 'line',
            smooth: true,
            itemStyle: {
              normal: {
                color: 'rgba(16, 97, 204, 1)',
                borderColor: 'rgba(17, 235, 210, 0.20)',
                borderWidth: 12,
              },
            },
          }
        ]
      };

      return option;
    },
  },
};
</script>
