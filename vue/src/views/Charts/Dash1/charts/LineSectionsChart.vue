<template>
  <v-chart
    style='width:100%;'
    :options='chartOption'
    autoresize
  />
</template>

<script>
import {getChart1} from "../../../../api/movie";

export default {
  name: 'SectionsLineChart',
  data() {
    return {
      chartOption: {},
      seriesData: [],
      xAxisData: []
    };
  },
  async mounted() {
    await getChart1().then(res => {
      this.seriesData = res.data.data.all.map(i => i.value)
      this.xAxisData = res.data.data.all.map(i => i.name)
    })
    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      const option = {
        // backgroundColor: '#394056',
        title: {
          // text: 'Line - Sections',
          // textStyle: {
          //   fontWeight: 'normal',
          //   fontSize: 20,
          //   color: '#F1F1F3',
          // },
        },
        legend: {
          show: true,
          textStyle: {
            fontWeight: 'normal',
            fontSize: 20,
            color: '#F1F1F3',
          },
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'cross',
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
          boundaryGap: false,
          data: this.xAxisData,
          axisLine: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        yAxis: {
          type: 'value',
          axisLabel: {
            // formatter: '{value} K',
            formatter: '{value}',
          },
          axisPointer: {
            snap: true,
          },
          axisLine: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        visualMap: {
          type: 'piecewise',
          show: false,
          dimension: 0,
          pieces: [
            // {
            //   lte: 7,
            //   color: 'green',
            // },
            // {
            //   gt: 7,
            //   lte: 10,
            //   color: 'red',
            // },
            // {
            //   gt: 10,
            //   lte: 17,
            //   color: 'green',
            // },
            // {
            //   gt: 17,
            //   lte: 22,
            //   color: 'red',
            // },
            // {
            //   gt: 22,
            //   color: 'green',
            // },
          ],
        },
        series: [
          {
            name: '上映情况',
            type: 'line',
            smooth: true,
            data: this.seriesData,
            markArea: {
              data: [
                [
                  {
                    name: '高峰',
                    xAxis: '1990-2000',
                  },
                  {
                    xAxis: '2010-2020',
                  },
                ],
                [
                  {
                    name: '低潮',
                    xAxis: '1960-1970',
                  },
                  {
                    xAxis: '1970-1980',
                  },
                ],
              ],
              itemStyle: {
                color: '#FF8A65',
              },
            },
          },
        ],
      };

      return option;
    },
  }
};
</script>
