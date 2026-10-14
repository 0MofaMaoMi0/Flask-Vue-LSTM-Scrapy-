<template>
  <v-chart
    style="width:100%;"
    :options="chartOption"
    autoresize
  />
</template>

<script>
import echarts from 'echarts/lib/echarts';
import {getChart1} from "../../../../api/movie";

export default {
  name: 'AreaLineChart',
  data() {
    return {
      chartOption: {},
      xAxisData: [],
      seriesAData: [],
      seriesBData: [],
      seriesCData: [],
      seriesDData: []
    };
  },
  async mounted() {
    await getChart1().then(res => {
      this.xAxisData = res.data.data.all.map(i => i.name)
      this.seriesAData = res.data.data.aq.map(i => i.value)
      this.seriesBData = res.data.data.dz.map(i => i.value)
      this.seriesCData = res.data.data.kh.map(i => i.value)
      this.seriesDData = res.data.data.xj.map(i => i.value)
    })

    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      const markPointObj = {
        data: [
          {
            type: 'max',
            name: '最大值',
          },
          {
            type: 'min',
            name: '最小值',
          },
        ],
      };

      const option = {
        // backgroundColor: '#394056',
        grid: {
          right: '3%',
          bottom: '4%',
          left: '3%',
          containLabel: true,
        },
        title: {
          // text: 'Line - Area',
          // textStyle: {
          //   fontWeight: 'normal',
          //   fontSize: 20,
          //   color: '#F1F1F3',
          // },
        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            lineStyle: {
              color: '#78909C',
            },
          },
        },
        toolbox: {
          show: true,
          right: 20,
          feature: {
            saveAsImage: {},
          },
        },
        legend: {
          icon: 'rect',
          itemGap: 15,
          itemWidth: 16,
          itemHeight: 10,
          data: ['爱情电影', '动作电影', '科幻电影','喜剧电影'],
          textStyle: {
            fontSize: 14,
            color: '#ECEFF1',
          },
        },
        xAxis: [
          {
            type: 'category',
            boundaryGap: false,
            axisLine: {
              lineStyle: {
                color: '#78909C',
              },
            },
            data: this.xAxisData,
          },
        ],
        yAxis: [
          {
            type: 'value',
            name: '上映数',
            axisTick: {
              show: false,
            },
            axisLine: {
              lineStyle: {
                color: '#78909C',
              },
            },
            splitLine: {
              lineStyle: {
                color: '#546E7A',
              },
            },
          },
        ],
        series: [
          {
            name: '爱情电影',
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            showSymbol: false,
            lineStyle: {
              normal: {
                width: 1,
              },
            },
            markPoint: markPointObj,
            areaStyle: {
              normal: {
                color: new echarts.graphic.LinearGradient(
                  0,
                  0,
                  0,
                  1,
                  [
                    {
                      offset: 0,
                      color: 'rgba(16, 97, 204, 0.3)',
                    },
                    {
                      offset: 0.8,
                      color: 'rgba(17, 235, 210, 0)',
                    },
                  ],
                  false,
                ),
                shadowColor: 'rgba(0, 0, 0, 0.1)',
                shadowBlur: 10,
              },
            },
            itemStyle: {
              normal: {
                color: 'rgba(16, 97, 204, 1)',
                borderColor: 'rgba(17, 235, 210, 0.20)',
                borderWidth: 12,
              },
            },
            data: this.seriesAData,
          },
          {
            name: '动作电影',
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            showSymbol: false,
            lineStyle: {
              normal: {
                width: 1,
              },
            },
            markPoint: markPointObj,
            areaStyle: {
              normal: {
                color: new echarts.graphic.LinearGradient(
                  0,
                  0,
                  0,
                  1,
                  [
                    {
                      offset: 0,
                      color: 'rgba(137, 189, 27, 0.3)',
                    },
                    {
                      offset: 0.8,
                      color: 'rgba(137, 189, 27, 0)',
                    },
                  ],
                  false,
                ),
                shadowColor: 'rgba(0, 0, 0, 0.1)',
                shadowBlur: 10,
              },
            },
            itemStyle: {
              normal: {
                color: 'rgb(137, 189, 27)',
                borderColor: 'rgba(137, 189, 2, 0.27)',
                borderWidth: 12,
              },
            },
            data: this.seriesBData,
          },
          {
            name: '科幻电影',
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 5,
            showSymbol: false,
            lineStyle: {
              normal: {
                width: 1,
              },
            },
            markPoint: markPointObj,
            areaStyle: {
              normal: {
                color: new echarts.graphic.LinearGradient(
                  0,
                  0,
                  0,
                  1,
                  [
                    {
                      offset: 0,
                      color: 'rgba(219, 50, 51, 0.3)',
                    },
                    {
                      offset: 0.8,
                      color: 'rgba(219, 50, 51, 0)',
                    },
                  ],
                  false,
                ),
                shadowColor: 'rgba(0, 0, 0, 0.1)',
                shadowBlur: 10,
              },
            },
            itemStyle: {
              normal: {
                color: 'rgb(219,50,51)',
                borderColor: 'rgba(219, 50, 51, 0.2)',
                borderWidth: 12,
              },
            },
            data: this.seriesCData,
          },
          {
            name: '喜剧电影',
            type: 'line',
            smooth: true,
            symbol: 'circle',
            symbolSize: 4,
            showSymbol: false,
            lineStyle: {
              normal: {
                width: 1,
              },
            },
            markPoint: markPointObj,
            areaStyle: {
              normal: {
                color: new echarts.graphic.LinearGradient(
                    0,
                    0,
                    0,
                    1,
                    [
                      {
                        offset: 0,
                        color: 'rgba(16, 97, 204, 0.3)',
                      },
                      {
                        offset: 0.8,
                        color: 'rgba(17, 235, 210, 0)',
                      },
                    ],
                    false,
                ),
                shadowColor: 'rgba(0, 0, 0, 0.1)',
                shadowBlur: 10,
              },
            },
            itemStyle: {
              normal: {
                color: 'rgb(85,153,242)',
                borderColor: 'rgba(17, 235, 210, 0.20)',
                borderWidth: 12,
              },
            },
            data: this.seriesDData,
          },
        ],
      };

      return option;
    },
  }
};
</script>
