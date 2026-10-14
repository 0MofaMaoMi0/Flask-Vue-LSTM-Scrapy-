<template>
  <div class="home">
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
      评分最高的电影
    </div>
      <v-row>
        <v-col cols="12" md="3" sm="6"
            v-for="(item, index) in movies" :key="index">
          <movie-card :movie="item"/>
        </v-col>
      </v-row>
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
       基于物品的协同过滤算法推荐电影（ItemCF）
    </div>

    <v-row>
      <v-col cols="12" md="3" sm="6"
             v-for="(item, index) in recs" :key="index">
        <movie-card :movie="item"/>
      </v-col>
    </v-row>
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
      基于用户的协同过滤算法推荐电影（UserCF）
    </div>
    <v-row>
      <v-col cols="12" md="3" sm="6"
             v-for="(item, index) in recs2" :key="index">
        <movie-card :movie="item"/>
      </v-col>
    </v-row>
  </div>
</template>

<script>
import MovieCard from "../components/MovieCard";
import {getHot, getItemCF, getUserCF} from "../api/movie";
import {mapState} from 'vuex'

export default {
  components: {  MovieCard },
  name: "Home",
  data: () => ({
    movies: [
      {
        name : '肖申克的救赎',
        rate : 9.7,
        type : '犯罪 剧情',
        image : 'https://img2.doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg',
        color : 'red',
        commentNum : 2478128,
        intro: '希望让人自由',
        url: 'https://movie.douban.com/subject/1292052/'
      },
      {
        name : '这个杀手不太冷',
        rate : 9.4,
        type : '剧情 动作 犯罪',
        image : 'https://img2.doubanio.com/view/photo/s_ratio_poster/public/p511118051.jpg',
        color : 'blue',
        commentNum : 2025581,
        intro: '怪蜀黍和小萝莉不得不说的故事'
      },
      {
        name : '海上钢琴师',
        rate : 9.3,
        type : '剧情 音乐',
        image : 'https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2574551676.jpg',
        color : 'yellow',
        commentNum : 1454616,
        intro: '每个人都要走一条自己坚定了的路，就算是粉身碎骨'
      },
      {
        name : '教父',
        rate : 9.3,
        type : '犯罪 剧情',
        image : 'https://img9.doubanio.com/view/photo/s_ratio_poster/public/p616779645.jpg',
        color : 'deep-purple accent-1',
        commentNum : 810666,
        intro: '千万不要记恨你的对手，这样会让你失去理智'
      },
    ],
    recs: [
      {
        name : '肖申克的救赎',
        rate : 9.7,
        type : '犯罪 剧情',
        image : 'https://img2.doubanio.com/view/photo/s_ratio_poster/public/p480747492.jpg',
        color : 'light-blue lighten-4'
      },
      {
        name : '这个杀手不太冷',
        rate : 9.4,
        type : '剧情 动作 犯罪',
        image : 'https://img2.doubanio.com/view/photo/s_ratio_poster/public/p511118051.jpg',
        color : 'cyan lighten-1'
      },
      {
        name : '海上钢琴师',
        rate : 9.3,
        type : '剧情 音乐',
        image : 'https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2574551676.jpg',
        color : 'lime lighten-2'
      },
      {
        name : '教父',
        rate : 9.3,
        type : '犯罪 剧情',
        image : 'https://img9.doubanio.com/view/photo/s_ratio_poster/public/p616779645.jpg',
        color : 'orange lighten-1'
      },
    ],
    recs2: []
  }),
  computed: {
    ...mapState(['uid'])
  },
  async mounted() {
    console.log('当前登录uid:' + this.uid)
    await getHot().then(res => {
      this.movies = res.data.data
    })
    await getItemCF(this.uid).then(res => {
      // console.log(res.data)
      this.recs2 = res.data.data.datas
    })
    await getUserCF(this.uid).then(res => {
      this.recs = res.data.data.datas
    })
  }
}
</script>

<style scoped>

</style>
