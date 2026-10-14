<template>
    <v-chart
        :options="options"
        autoresize
        style="width:100%; min-height:650px;"
    >
    </v-chart>
</template>

<script>
import Echarts from 'vue-echarts'
import 'echarts/lib/component/geo'
import 'echarts/lib/chart/map'
import World from '@/assets/world.zh.json'
// import axios from 'axios'
import {getNationRank} from "@/api/movie";

const data = []

getNationRank().then(res => {
  res.data.data.datas.map(i => data.push(i))
})

// axios.get('https://api.covid19api.com/summary').then((res) => {
//   // const array = res.data.Countries
//   // const group = Object.keys(array).map((Country) => {
//   //   return {
//   //     name: array[Country].Country,
//   //     value: array[Country].TotalConfirmed
//   //   }
//   // })
//   // data.push(...group)
//   console.log(res)
//   const g = new Object()
//   g.name = '中国'
//   g.value = 1000
//   data.push(g)
// })


Echarts.registerMap('World', World)

export default {
  components: {
    'v-chart': Echarts
  },
  data() {
    return {
      options: {
        tooltip: {
          trigger: 'item',
          formatter: '{b}<br/>{c} 部.'
        },
        toolbox: {
          show: true,
          orient: 'vertical',
          itemSize: 30,
          itemGap: 20,
          right: 15,
          iconStyle: {
            borderColor: '#66BB6A',
            borderWidth: 1
          },
          top: 'center',
          feature: {
            dataView: {
              show: true,
              readOnly: true,
              title: 'View data in table.',
              lang: ['Data view', 'close'],
              backgroundColor: '#B39DDB',
              buttonColor: '#512DA8'
            },
            restore: {
              show: true,
              title: 'Reset Zoom'
            },
            saveAsImage: {
              show: true,
              name: 'Choropleth Map',
              title: 'Save as image',
              type: 'png',
              pixelRatio: 10
            }
          }
        },
        visualMap: {
          min: 0,
          max: 60,
          textStyle:{
            // fontSize: 20,
            color: '#F1F1F3'
          },
          text: ['High', 'Low'],
          realtime: true,
          calculable: true,
          inRange: {
            color: ['#66BB6A', '#C8E6C9', '#FFD54F', '#81D4FA', '#0288D1', '#B71C1C']
          },
          outOfRange: {
            color: ['#FFCDD2', '#FF5722']
          }
        },
        series: [
          {
            name: '世界各国TOP250电影分布 Map',
            type: 'map',
            mapType: 'World',
            roam: true,
            label: {
              show: false
            },
            data: data,
            dataZoom: {
              type: 'inside'
            },
            nameMap: {
                '中国': '中国大陆',
            }
            // nameMap: {
            //   'Afghanistan': 'Afghanistan',
            //   'Albania': 'Albania',
            //   'Algeria': 'Algeria',
            //   'Andorra': 'Andorra',
            //   'Angola': 'Angola',
            //   'Antigua and Barb.': 'Antigua and Barbuda',
            //   'Argentina': 'Argentina',
            //   'Armenia': 'Armenia',
            //   'Australia': 'Australia',
            //   'Austria': 'Austria',
            //   'Azerbaijan': 'Azerbaijan',
            //   'Bahamas': 'Bahamas',
            //   'Bahrain': 'Bahrain',
            //   'Bangladesh': 'Bangladesh',
            //   'Barbados': 'Barbados',
            //   'Belarus': 'Belarus',
            //   'Belgium': 'Belgium',
            //   'Belize': 'Belize',
            //   'Benin': 'Benin',
            //   'Bhutan': 'Bhutan',
            //   'Bolivia': 'Bolivia',
            //   'Bosnia and Herz.': 'Bosnia and Herzgovina',
            //   'Botswana': 'Botswana',
            //   'Brazil': 'Brazil',
            //   'Brunei': 'Brunei Darussalam',
            //   'Bulgaria': 'Bulgaria',
            //   'Burkina Faso': 'Burkina Faso',
            //   'Burundi': 'Burundi',
            //   'Cambodia': 'Cambodia',
            //   'Cameroon': 'Cameroon',
            //   'Canada': 'Canada',
            //   'Cape Verde': 'Cape Verde',
            //   'Central African Rep.': 'Central African Republic',
            //   'Chad': 'Chad',
            //   'Chile': 'Chile',
            //   '中国': 'China',
            //   'Colombia': 'Colombia',
            //   'Comoros': 'Comoros',
            //   'Congo': 'Congo (Brazzaville)',
            //   'Costa Rica': 'Costa Rica',
            //   'Croatia': 'Croatia',
            //   'Cuba': 'Cuba',
            //   'Cyprus': 'Cyprus',
            //   'Czech Rep.': 'Czech Republic',
            //   '`Côte d`Ivoire': '`Côte d`Ivoire',
            //   'Dem. Rep. Congo': 'Congo (Kinshasa)',
            //   'Denmark': 'Denmark',
            //   'Djibouti': 'Djibouti',
            //   'Dominica': 'Dominica',
            //   'Dominican Rep.': 'Dominican Republic',
            //   'Ecuador': 'Ecuador',
            //   'Egypt': 'Egypt',
            //   'El Salvador': 'El Salvador',
            //   'Eq. Guinea': 'Equatorial Guinea',
            //   'Eritrea': 'Eritrea',
            //   'Estonia': 'Estonia',
            //   'Ethiopia': 'Ethiopia',
            //   'Fiji': 'Fiji',
            //   'Finland': 'Finland',
            //   'France': 'France',
            //   'Gabon': 'Gabon',
            //   'Gambia': 'Gambia',
            //   'Georgia': 'Georgia',
            //   'Germany': 'Germany',
            //   'Ghana': 'Ghana',
            //   'Greece': 'Greece',
            //   'Grenada': 'Grenada',
            //   'Guatemala': 'Guatemala',
            //   'Guinea': 'Guinea',
            //   'Guinea-Bissau': 'Guinea-Bissau',
            //   'Guyana': 'Guyana',
            //   'Haiti': 'Haiti',
            //   'Honduras': 'Honduras',
            //   'Hungary': 'Hungary',
            //   'Iceland': 'Iceland',
            //   'India': 'India',
            //   'Indonesia': 'Indonesia',
            //   'Iran': 'Iran, Islamic Republic of',
            //   'Iraq': 'Iraq',
            //   'Ireland': 'Ireland',
            //   'Israel': 'Israel',
            //   'Italy': 'Italy',
            //   'Jamaica': 'Jamaica',
            //   'Japan': 'Japan',
            //   'Jordan': 'Jordan',
            //   'Kazakhstan': 'Kazakhstan',
            //   'Kenya': 'Kenya',
            //   'Korea': 'Korea (South)',
            //   'Kuwait': 'Kuwait',
            //   'Kyrgyzstan': 'Kyrgyzstan',
            //   'Lao PDR': 'Lao PDR',
            //   'Latvia': 'Latvia',
            //   'Lebanon': 'Lebanon',
            //   'Lesotho': 'Lesotho',
            //   'Liberia': 'Liberia',
            //   'Libya': 'Libya',
            //   'Liechtenstein': 'Liechtenstein',
            //   'Lithuania': 'Lithuania',
            //   'Luxembourg': 'Luxembourg',
            //   'Macedonia': 'Macedonia, Republic of',
            //   'Madagascar': 'Madagascar',
            //   'Malawi': 'Malawi',
            //   'Malaysia': 'Malaysia',
            //   'Mali': 'Mali',
            //   'Malta': 'Malta',
            //   'Mauritania': 'Mauritania',
            //   'Mauritius': 'Mauritius',
            //   'Mexico': 'Mexico',
            //   'Moldova': 'Moldova',
            //   'Mongolia': 'Mongolia',
            //   'Montenegro': 'Montenegro',
            //   'Morocco': 'Morocco',
            //   'Mozambique': 'Mozambique',
            //   'Myanmar': 'Myanmar',
            //   'Namibia': 'Namibia',
            //   'Nepal': 'Nepal',
            //   'Netherlands': 'Netherlands',
            //   'New Zealand': 'New Zealand',
            //   'Nicaragua': 'Nicaragua',
            //   'Niger': 'Niger',
            //   'Nigeria': 'Nigeria',
            //   'Norway': 'Norway',
            //   'Oman': 'Oman',
            //   'Pakistan': 'Pakistan',
            //   'Palestine': 'Palestinian Territory',
            //   'Panama': 'Panama',
            //   'Papua New Guinea': 'Papua New Guinea',
            //   'Paraguay': 'Paraguay',
            //   'Peru': 'Peru',
            //   'Philippines': 'Philippines',
            //   'Poland': 'Poland',
            //   'Portugal': 'Portugal',
            //   'Qatar': 'Qatar',
            //   'Romania': 'Romania',
            //   'Russia': 'Russian Federation',
            //   'Rwanda': 'Rwanda',
            //   'Saint Lucia': 'Saint Lucia',
            //   'Saudi Arabia': 'Saudi Arabia',
            //   'Senegal': 'Senegal',
            //   'Serbia': 'Serbia',
            //   'Seychelles': 'Seychelles',
            //   'Sierra Leone': 'Sierra Leone',
            //   'Singapore': 'Singapore',
            //   'Slovakia': 'Slovakia',
            //   'Slovenia': 'Slovenia',
            //   'Somalia': 'Somalia',
            //   'South Africa': 'South Africa',
            //   'S. Sudan': 'South Sudan',
            //   'Spain': 'Spain',
            //   'Sri Lanka': 'Sri Lanka',
            //   'Sudan': 'Sudan',
            //   'Suriname': 'Suriname',
            //   'Swaziland': 'Swaziland',
            //   'Sweden': 'Sweden',
            //   'Switzerland': 'Switzerland',
            //   'Syria': 'Syrian Arab Republic (Syria)',
            //   'Tajikistan': 'Tajikistan',
            //   'Tanzania': 'Tanzania, United Republic of',
            //   'Thailand': 'Thailand',
            //   'Timor-Leste': 'Timor-Leste',
            //   'Togo': 'Togo',
            //   'Trinidad and Tobago': 'Trinidad and Tobago',
            //   'Tunisia': 'Tunisia',
            //   'Turkey': 'Turkey',
            //   'Uganda': 'Uganda',
            //   'Ukraine': 'Ukraine',
            //   'United Arab Emirates': 'United Arab Emirates',
            //   'United Kingdom': 'United Kingdom',
            //   'United States': 'United States of America',
            //   'Uruguay': 'Uruguay',
            //   'Uzbekistan': 'Uzbekistan',
            //   'Venezuela': 'Venezuela (Bolivarian Republic)',
            //   'Vietnam': 'Viet Nam',
            //   'W. Sahara': 'Western Sahara',
            //   'Yemen': 'Yemen',
            //   'Zambia': 'Zambia',
            //   'Zimbabwe': 'Zimbabwe'
            // }
          }
        ]
      }
    }
  }
}
</script>
