<template>
  <div class="home">
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
      评分最高的电影
    </div>
      <v-row>
        <v-col cols="12" md="3" sm="6"
            v-for="(item, index) in movies" :key="item.douban_id || index">
          <movie-card :movie="item"/>
        </v-col>
      </v-row>
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
       基于物品的协同过滤算法推荐电影（ItemCF）
    </div>

    <v-row>
      <v-col cols="12" md="3" sm="6"
             v-for="(item, index) in recs" :key="item.douban_id || index">
        <movie-card :movie="item"/>
      </v-col>
    </v-row>
    <div class="white--text font-weight-bold text-h5 d-flex justify-start my-6">
      基于用户的协同过滤算法推荐电影（UserCF）
    </div>
    <v-row>
      <v-col cols="12" md="3" sm="6"
             v-for="(item, index) in recs2" :key="item.douban_id || index">
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
    // 列表只承载接口数据：之前这里写死过 4 部演示影片，接口返回后
    // 若与写死数据混用，会出现「卡片标题来自演示数据、详情链接指向
    // 另一部片」的错位。现在一律以接口为准。
    movies: [],
    recs: [],
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
