<template>
  <v-chart
      style="width:100%; min-height:650px;"
      :options="chartOption"
      autoresize
  />
</template>

<script>
import {getTimeLine} from "@/api/movie";

import echarts from "echarts/lib/echarts";

export default {
  name: 'BaseBarChart',
  data() {
    return {
      chartOption: {},
      dataMap: {
        month: []
      },
    };
  },
  async mounted() {
    await getTimeLine().then(res=>{
      const a = res.data.data.datas
      // console.log(a)
      this.dataMap.dataGDP = this.dataFormatter({
        //max : 60000,
        2000:a[0],2001:a[1],2002:a[2],2003:a[3],2004:a[4],
        2005:a[5],2006:a[6],2007:a[7],2008:a[8],2009:a[9],
        2010:a[10],2011:a[11],2012:a[12],2013:a[13],2014:a[14],
        2015:a[15],2016:a[16],2017:a[17],2018:a[18],2019:a[19],
        2020:a[20]
      });
      // console.log(this.dataMap.dataGDP)
    })

    console.log(this.dataMap.dataGDP['2000'])
    console.log(this.dataMap.dataGDP['2000'])
    this.chartOption = this.buildChartOption();
  },
  methods: {
    buildChartOption() {
      const option = {
        timeline:{
          data:[
            '2000','2001','2002','2003','2004','2005','2006', '2007','2008','2009','2010','2011',
            '2012','2013','2014','2015','2016','2017','2018', '2019','2020'
          ],
          label : {
            textStyle:{
              color: '#F1F1F3'
            },
            formatter : function(s) {
              return new Date(s).getFullYear();
            },
          },
          autoPlay : false,
          playInterval : 1000,
        },
        options:[
          {
            tooltip : {'trigger':'axis'},
            legend : {
              x:'right',
              'data':['电影数量'],
              'selected':{
                '电影数量':true
              },
              textStyle: {
                fontWeight: 'normal',
                fontSize: 20,
                color: '#F1F1F3',
              },
            },
            toolbox : {
              'show':false,
              orient : 'vertical',
              x: 'right',
              y: 'center',
              'feature':{
                'mark':{'show':true},
                'dataView':{'show':true,'readOnly':false},
                'magicType':{'show':true,'type':['line','bar','stack','tiled']},
                'restore':{'show':true},
                'saveAsImage':{'show':true}
              }
            },
            calculable : true,
            grid : {'y':60,'y2':90},
            xAxis : [{
              'type':'category',
              'axisLabel':{'interval':0},
              'data':[
                '美国', '英国', '日本', '中国香港', '中国大陆', '法国', '德国', '韩国', '意大利', '加拿大'
                ,'中国台湾','澳大利亚','西班牙','印度','瑞士','新西兰'
              ],
              axisLine: {
                lineStyle: {
                  color: '#78909C',
                },
              },
            }],
            yAxis : [
              {
                'type':'value',
                'name':'电影数量（部）',
                'max':10,
                axisLine: {
                  lineStyle: {
                    color: '#78909C',
                  },
                },
              }
            ],
            series : [
              {
                'name':'2000年各国电影数',
                'type':'bar',
                barWidth: '20%',
                itemStyle: {
                  normal: {
                    barBorderRadius: 99,
                    color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                      {
                        offset: 0,
                        color: '#CCFF90',
                      },
                      {
                        offset: 0.4,
                        color: '#B2FF59',
                      },
                      {
                        offset: 0.7,
                        color: '#76FF03',
                      },
                      {
                        offset: 1,
                        color: '#64DD17',
                      },
                    ]),
                  },
                },
                'markLine':{
                  symbol : ['arrow','none'],
                  symbolSize : [4, 2],
                  itemStyle : {
                    normal: {
                      lineStyle: {color:'orange'},
                      barBorderColor:'orange',
                      label:{
                        position:'left',
                        formatter:function(params){
                          return Math.round(params.value);
                        },
                        textStyle:{color:'orange'}
                      }
                    }
                  },
                  'data':[{'type':'average','name':'平均值'}]
                },
                'data': this.dataMap.dataGDP['2000']
              }
            ]
          },
          {
            title : {'text':'2001年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2001']}]
          },
          {
            title : {'text':'2002年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2002']}]
          },
          {
            title : {'text':'2003年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2003']}]
          },
          {
            title : {'text':'2004年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2004']}]
          },
          {
            title : {'text':'2005年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2005']}]
          },
          {
            title : {'text':'2006年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2006']}]
          },
          {
            title : {'text':'2007年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2007']}]
          },
          {
            title : {'text':'2008年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2008']}]
          },
          {
            title : {'text':'2009年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2009']}]
          },
          {
            title : {'text':'2010年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2010']}]
          },
          {
            title : {'text':'2011年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2011']}]
          },
          {
            title : {'text':'2012年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2012']}]
          },
          {
            title : {'text':'2013年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2013']}]
          },
          {
            title : {'text':'2014年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2014']}]
          },
          {
            title : {'text':'2015年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2015']}]
          },
          {
            title : {'text':'2016年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2016']}]
          },
          {
            title : {'text':'2017年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2017']}]
          },
          {
            title : {'text':'2018年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2018']}]
          },
          {
            title : {'text':'2019年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2019']}]
          },
          {
            title : {'text':'2020年各国电影数'},
            series : [{'data': this.dataMap.dataGDP['2020']}]
          },
        ]
      };
      return option;
    },

    dataFormatter(obj) {
      var pList = [
        '美国', '英国', '日本', '中国香港', '中国大陆', '法国', '德国', '韩国', '意大利', '加拿大'
        ,'中国台湾','澳大利亚','西班牙','印度','瑞士','新西兰'
      ];
      var temp;
      var max = 0;
      for (var year = 2000; year <= 2020; year++) {
        temp = obj[year];
        for (var i = 0, l = temp.length; i < l; i++) {
          max = Math.max(max, temp[i]);
          obj[year][i] = {
            name: pList[i],
            value: temp[i]
          }
        }
        obj[year + 'max'] = Math.floor(max / 100) * 100;
      }
      return obj;
    },

    initData(){




      // this.dataMap.dataGDP = this.dataFormatter({
      //   //max : 60000,
      //   2011:[16251.93,11307.28,24515.76,11237.55,14359.88,22226.7,10568.83,12582,19195.69,49110.27,32318.85,15300.65,17560.18,11702.82,45361.85,26931.03,19632.26,19669.56,53210.28,11720.87,2522.66,10011.37,21026.68,5701.84,8893.12,605.83,12512.3,5020.37,1670.44,2102.21,6610.05],
      //   2010:[14113.58,9224.46,20394.26,9200.86,11672,18457.27,8667.58,10368.6,17165.98,41425.48,27722.31,12359.33,14737.12,9451.26,39169.92,23092.36,15967.61,16037.96,46013.06,9569.85,2064.5,7925.58,17185.48,4602.16,7224.18,507.46,10123.48,4120.75,1350.43,1689.65,5437.47],
      //   2009:[12153.03,7521.85,17235.48,7358.31,9740.25,15212.49,7278.75,8587,15046.45,34457.3,22990.35,10062.82,12236.53,7655.18,33896.65,19480.46,12961.1,13059.69,39482.56,7759.16,1654.21,6530.01,14151.28,3912.68,6169.75,441.36,8169.8,3387.56,1081.27,1353.31,4277.05],
      //   2008:[11115,6719.01,16011.97,7315.4,8496.2,13668.58,6426.1,8314.37,14069.87,30981.98,21462.69,8851.66,10823.01,6971.05,30933.28,18018.53,11328.92,11555,36796.71,7021,1503.06,5793.66,12601.23,3561.56,5692.12,394.85,7314.58,3166.82,1018.62,1203.92,4183.21],
      //   2007:[9846.81,5252.76,13607.32,6024.45,6423.18,11164.3,5284.69,7104,12494.01,26018.48,18753.73,7360.92,9248.53,5800.25,25776.91,15012.46,9333.4,9439.6,31777.01,5823.41,1254.17,4676.13,10562.39,2884.11,4772.52,341.43,5757.29,2703.98,797.35,919.11,3523.16],
      //   2006:[8117.78,4462.74,11467.6,4878.61,4944.25,9304.52,4275.12,6211.8,10572.24,21742.05,15718.47,6112.5,7583.85,4820.53,21900.19,12362.79,7617.47,7688.67,26587.76,4746.16,1065.67,3907.23,8690.24,2338.98,3988.14,290.76,4743.61,2277.35,648.5,725.9,3045.26],
      //   2005:[6969.52,3905.64,10012.11,4230.53,3905.03,8047.26,3620.27,5513.7,9247.66,18598.69,13417.68,5350.17,6554.69,4056.76,18366.87,10587.42,6590.19,6596.1,22557.37,3984.1,918.75,3467.72,7385.1,2005.42,3462.73,248.8,3933.72,1933.98,543.32,612.61,2604.19],
      //   2004:[6033.21,3110.97,8477.63,3571.37,3041.07,6672,3122.01,4750.6,8072.83,15003.6,11648.7,4759.3,5763.35,3456.7,15021.84,8553.79,5633.24,5641.94,18864.62,3433.5,819.66,3034.58,6379.63,1677.8,3081.91,220.34,3175.58,1688.49,466.1,537.11,2209.09],
      //   2003:[5007.21,2578.03,6921.29,2855.23,2388.38,6002.54,2662.08,4057.4,6694.23,12442.87,9705.02,3923.11,4983.67,2807.41,12078.15,6867.7,4757.45,4659.99,15844.64,2821.11,713.96,2555.72,5333.09,1426.34,2556.02,185.09,2587.72,1399.83,390.2,445.36,1886.35],
      //   2002:[4315,2150.76,6018.28,2324.8,1940.94,5458.22,2348.54,3637.2,5741.03,10606.85,8003.67,3519.72,4467.55,2450.48,10275.5,6035.48,4212.82,4151.54,13502.42,2523.73,642.73,2232.86,4725.01,1243.43,2312.82,162.04,2253.39,1232.03,340.65,377.16,1612.6]
      // });
    }
  },
};
</script>
