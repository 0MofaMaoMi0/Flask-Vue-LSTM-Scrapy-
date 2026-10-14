<template>
  <v-chart
    style="width:100%;"
    :options="chartOption"
    autoresize
  />
</template>

<script>
import {getAreaChart} from "../../../../api/movie";

export default {
  name: 'RectAreaLineChart',
  data() {
    return {
      chartOption: {},
      xAxisData: [],
      seriesData: []
    };
  },
  async mounted() {
    await getAreaChart().then(res => {
      this.xAxisData = res.data.data.kh.map(i => i.name)
      this.seriesData = res.data.data.kh.map(i => i.value)
    })

    // console.log(this.seriesData)
    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      const legendData = []
      this.xAxisData.forEach(i=>{
        let o = new Object()
        o.name = i
        o.icon = 'roundRect'
        legendData.push(o)
      })

      // const legendData = [
      //   {
      //     name: 'A',
      //     icon: 'roundRect',
      //   },
      //   {
      //     name: 'B',
      //     icon: 'roundRect',
      //   },
      //   {
      //     name: 'C',
      //     icon: 'roundRect',
      //   },
      //   {
      //     name: 'D',
      //     icon: 'roundRect',
      //   },
      //   {
      //     name: 'E',
      //     icon: 'roundRect',
      //   },
      // ];

      const option = {
        // backgroundColor: '#394056',
        color: ['#84FFFF', '#69F0AE', '#F4511E', '#EEFF41', '#FF6F00'],
        title: {
          // text: 'Line - Rect Area',
          // textStyle: {
          //   fontWeight: 'normal',
          //   fontSize: 20,
          //   color: '#F1F1F3',
          // },
        },
        grid: {
          right: 60,
          left: 20,
          bottom: '2%',
          containLabel: true,
        },
        toolbox: {
          show: true,
          right: 20,
          feature: {
            saveAsImage: {},
          },
        },
        legend: {
          // orient: 'vertical',
          top: 6,
          right: 50,
          data: legendData,
          textStyle: {
            fontWeight: 'normal',
            fontSize: 20,
            color: '#F1F1F3',
          },
        },
        xAxis: [
          {
            type: 'value',
            splitLine: {
              show: false,
            },
            axisTick: {
              show: false,
            },
            axisLine: {
              lineStyle: {
                color: '#78909C',
              },
            },
          },
        ],
        yAxis: [
          {
            type: 'value',
            splitLine: {
              show: false,
            },
            axisTick: {
              show: false,
            },
            axisLine: {
              lineStyle: {
                color: '#78909C',
              },
            },
          },
        ],
        series: [
          {
            type: 'line',
            name: legendData[0].name,
            symbol: 'none',
            z: 5,
            lineStyle: {
              width: 0,
              color: '#84FFFF',
            },
            areaStyle: {
              color: '#84FFFF',
            },
            step: true,
            connectNulls: true,
            data: [[0, this.seriesData[0]], [1, this.seriesData[0]]],
          },
          {
            type: 'line',
            name: legendData[1].name,
            symbol: 'none',
            z: 4,
            lineStyle: {
              width: 0,
              color: '#69F0AE',
            },
            areaStyle: {
              color: '#69F0AE',
            },
            step: true,
            connectNulls: true,
            data: [[0, this.seriesData[1]], [2, this.seriesData[1]]],
          },
          {
            type: 'line',
            name: legendData[2].name,
            symbol: 'none',
            z: 3,
            lineStyle: {
              width: 0,
              color: '#F4511E',
            },
            areaStyle: {
              color: '#F4511E',
            },
            step: true,
            connectNulls: true,
            data: [[0, this.seriesData[2]], [3, this.seriesData[2]]],
          },
          {
            type: 'line',
            name: legendData[3].name,
            symbol: 'none',
            z: 2,
            lineStyle: {
              width: 0,
              color: '#EEFF41',
            },
            areaStyle: {
              color: '#EEFF41',
            },
            step: true,
            connectNulls: true,
            data: [[0, this.seriesData[3]], [4, this.seriesData[3]]],
          },
          {
            type: 'line',
            name: legendData[4].name,
            symbol: 'none',
            z: 1,
            lineStyle: {
              width: 0,
              color: '#FF6F00',
            },
            areaStyle: {
              color: '#FF6F00',
            },
            step: true,
            connectNulls: true,
            data: [[0, this.seriesData[4]], [5, this.seriesData[4]]],
          },
        ],
      };

      return option;
    },
  }
};
</script>
