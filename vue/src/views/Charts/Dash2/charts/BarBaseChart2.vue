<template>
  <v-chart
    style="width:100%;"
    :options="chartOption"
    autoresize
    ref="mychart"
  />
</template>

<script>
import {getTypeRank} from "@/api/movie";
import echarts from "echarts/lib/echarts";

export default {
  name: 'BaseBarChart',
  data() {
    return {
      chartOption: {},
      d1 :[],
      l1 :[],
      startValue: 0, // 区域缩放的起点值
      endValue: 9, // 区域缩放的终点值
      timer: null, // 定时器的标识
    };
  },
  async mounted() {
    await getTypeRank().then(res => {
      this.d1 = res.data.data.datas.map(i => i.value)
      this.l1 = res.data.data.datas.map(i => i.name)
    })
    this.chartOption = this.buildChartOption();
    this.startInterval();
  },
  destroyed() {
    clearInterval(this.timer);

  },
  methods: {
    buildChartOption() {
      const seriesData = this.d1;
      const option = {
        dataZoom: {
          show: false,
          startValue: this.startValue,
          endValue: this.endValue,
        },
        grid: {
          right: '3%',
          bottom: '4%',
          left: '3%',
          containLabel: true,
        },
        title: {

        },
        tooltip: {
          trigger: 'axis',
          axisPointer: {
            type: 'shadow',
          },
        },
        xAxis: {
          type: 'category',
          data: this.l1,
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
            data: seriesData,
            type: 'bar',
            barWidth: '35%',
            barGap: '-100%',
            itemStyle: {
              normal: {
                barBorderRadius: 30,
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  {
                    offset: 0,
                    color: '#18FFFF',
                  },
                  {
                    offset: 0.5,
                    color: '#29B6F6',
                  },
                  {
                    offset: 1,
                    color: '#2962FF',
                  },
                ]),
              },
            },
          }
        ],
      };
      return option;
    },
    startInterval() {
      if (this.timer) {
        clearInterval(this.timer);
      }

      this.timer = setInterval(() => {
        this.startValue++;
        this.endValue++;
        if (this.endValue > this.d1.length - 1) {
          this.startValue = 0;
          this.endValue = 9;
        }
        // this.updateChart();
        this.chartOption = this.buildChartOption();
      }, 2000);
    },
  },
};
</script>
