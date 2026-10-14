<template>
  <v-chart
      style="width:100%;"
      :options="chartOption"
      autoresize
  />
</template>

<script>
import {getChart1} from "../../../../api/movie";

export default {
  name: 'BaseLineChart',
  data() {
    return {
      chartOption: {},
      datas: [],
      labels: []
    };
  },
  async mounted() {
    await getChart1().then(res => {
      this.datas = res.data.data.all.map(i => i.value)
      this.labels = res.data.data.all.map(i => i.name)
    })
    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      const option = {
        // backgroundColor: '#394056',
        title: {
          // text: 'Line - Base',
          // textStyle: {
          //   fontWeight: 'normal',
          //   fontSize: 20,
          //   color: '#F1F1F3',
          // },
        },
        legend: {
          show: true,
          textStyle: {
            fontSize: 14,
            color: '#ECEFF1',
          },
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'line',
          },
        },
        toolbox: {
          show: true,
          right: 20,
          feature: {
            saveAsImage: {},
          },
        },
        xAxis: {
          type: 'category',
          // data: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'],
          data: this.labels,
          axisLine: {
            lineStyle: {
              color: '#78909C',
            },
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
        series: [
          {
            name: '所有类型',
            data: this.datas,
            type: 'line',
            smooth: true,
            itemStyle: {
              color: '#29B6F6',
            },
          },
          // {
          //   name: 'Smooth Line',
          //   data: [900, 667, 1232, 1058, 760, 1720, 1590],
          //   type: 'line',
          //   smooth: true,
          //   itemStyle: {
          //     color: '#29B6F6',
          //   },
          // },
          // {
          //   name: 'Step Line',
          //   type: 'line',
          //   step: 'middle',
          //   data: [420, 342, 401, 534, 690, 530, 710],
          // },
        ],
      };

      return option;
    },
  }
};
</script>
