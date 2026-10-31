<template>
  <div class="home">
    <v-row>
      <v-col md="3" cols="12">
        <search-box @search-movie="handleSearchResult" />
      </v-col>
    </v-row>
    <v-row>
      <v-col cols="12" md="3" sm="6"
             v-for="(item, index) in movies" :key="item.douban_id || index">
        <movie-card  :movie="item"/>
      </v-col>
    </v-row>
  </div>
</template>

<script>
import MovieCard from "../components/MovieCard";
import {get} from "../api/movie";
import SearchBox from "@/components/SearchBox.vue";

export default {
  components: {MovieCard, SearchBox},
  name: "Home",
  data: () => ({
    // 同上：不再预置写死的演示影片，避免标题与详情链接错位。
    movies: []
  }),
  async mounted() {
    await get({"keyword": ''}).then(res => {
      // console.log(res.data)
      this.movies = res.data.data
    })
  },
  methods: {
    handleSearchResult(result){
      // console.log(result)
      this.movies = result
    }
  }
}
</script>

<style scoped>

</style>
