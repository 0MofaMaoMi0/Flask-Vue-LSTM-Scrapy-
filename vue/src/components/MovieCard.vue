<template>
  <v-card
      :loading="loading"
      class="mx-auto my-12"
      max-width="374"
  >
    <template slot="progress">
      <v-progress-linear
          color="deep-purple"
          height="10"
          indeterminate
      ></v-progress-linear>
    </template>

    <v-img
        height="250"
        :src="posterUrl(movie)"
        :lazy-src="posterPlaceholder"
        @error="usePosterPlaceholder"
    ></v-img>

    <v-card-title>{{movie.name}}
      <span class="ml-3" style="font-size: 2px;">{{movie.alias}} </span>
    </v-card-title>

    <v-card-text>
      <v-row
          align="center"
          class="mx-0"
      >
        <v-rating
            :value="movie.douban_score/2.0"
            color="amber"
            dense
            half-increments
            readonly
            size="14"
        ></v-rating>

        <div class="grey--text ms-4">
          {{movie.douban_score}} ({{movie.douban_votes}})
        </div>
      </v-row>

      <div class="my-4 text-subtitle-1" v-if="genreRegion(movie)">
        {{genreRegion(movie)}}
      </div>

      <div class="my-4 text-subtitle-1" v-if="movie.directors">
        {{movie.directors}}
      </div>

      <div v-if="movie.storyline">{{shortText(movie.storyline, 90)}}</div>
    </v-card-text>

    <v-divider class="mx-4"></v-divider>

    <v-card-title>介绍</v-card-title>

    <v-card-text>
      <div class="my-4 text-subtitle-2" v-if="movie.actors">
       演员： {{movie.actors.substring(0,20)}}..等
      </div>
      <div class="my-4 text-subtitle-2" v-if="movie.directors">
       导演： {{movie.directors}}
      </div>
      <div class="my-4 text-subtitle-3" v-if="movie.storyline">
        简介： {{movie.storyline.substring(0,50)}}...
      </div>
    </v-card-text>

    <v-card-actions>
      <v-btn
          color="deep-purple lighten-2"
          text
          @click="reserve(movie.douban_id)">
        详情
      </v-btn>

      <v-dialog
          v-model="dialog"
          scrollable
          max-width="900"
      >
        <template v-slot:activator="{ on, attrs }">
          <v-btn
              color="primary"
              text
              v-bind="attrs"
              v-on="on"
              @click="getCommentsByDoubanId(movie.douban_id)"
          >
            影评情感分析
          </v-btn>
        </template>
        <v-card :loading="tableLoading">
          <v-card-title>精选影评</v-card-title>
          <v-divider></v-divider>
          <v-card-text style="height: 500px">
            <v-simple-table >
              <template v-slot:default>
            <thead>
            <tr>
              <th class="text-left">
                头像(avatar)
              </th>
              <th class="text-left">
                用户(user)
              </th>
              <th class="text-left">
                评论(comment)
              </th>
              <th class="text-left">
                情感分类(label)
              </th>
              <th class="text-left">
                概率(prob0)
              </th>
            </tr>
            </thead>
            <tbody>
            <tr
                v-for="(item,index) in comments"
                :key="index"
            >
              <td><img :src="item.douban_user_avatar"/></td>
              <td>{{ item.douban_user_nickname }}</td>
              <td>{{ item.content }}</td>
              <td>
                <div v-if="item.label=='positive'">
                  好评
                  <v-icon
                      large
                      color="green"
                  >
                    mdi-thumb-up
                  </v-icon>
                </div>
                <div v-if="item.label=='negative'">
                  差评
                  <v-icon
                      large
                      color="red"
                  >
                    mdi-thumb-down
                  </v-icon>
                </div>
              </td>
              <td>{{ item.score * 100}}%</td>
            </tr>
            </tbody>
              </template>
            </v-simple-table>
          </v-card-text>
          <v-divider></v-divider>
          <v-card-actions>
            <v-btn
                color="teal darken-1"
                text
                @click="dialog = false"
            >
              关闭
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-dialog>
    </v-card-actions>
  </v-card>
</template>

<script>
import {getComments} from '@/api/movie'
import posterPlaceholder from '@/assets/images/poster-placeholder.svg'

export default {
  name: "movie-card",
  props: {
    movie: Object,
    cardTitle: String
  },
  data: () => ({
    loading: false,
    tableLoading: false,
    selection: -1,
    dialog: false,
    comments: [],
    posterPlaceholder
    // comments: [{'comment':'这部电影还不错的哦,如果是很多很多字怎么说呢，嗯呢？？？？？‘','label':'positive','score':98.112},
    //   {'comment':'属实不知道该怎么去评价，有点不知所云','label':'negative','score':91.23}],
  }),
  methods: {
    doubanImg(src) {
      // 数据库已经保存完整的豆瓣海报 URL。旧实现强制转发到 images.weserv.nl，
      // 该代理已对这些地址返回 404，导致整页海报空白。
      return src || this.posterPlaceholder
    },
    posterUrl(movie) {
      // 本地已存好的 WebP 直接引用；其余（豆瓣外链）走后端按需回源，
      // 由服务端带上豆瓣 Referer 取图，浏览器不会再被防盗链拦下。
      const cover = (movie && movie.cover) || ''
      if (cover.startsWith('/api/media/covers/')) {
        return cover
      }
      if (cover && movie.douban_id) {
        return '/api/media/poster/' + movie.douban_id
      }
      return this.posterPlaceholder
    },
    usePosterPlaceholder(event) {
      if (event && event.target) {
        event.target.src = this.posterPlaceholder
      }
    },
    // 类型与国家都为空时不要留下一个孤零零的「·」
    genreRegion(movie) {
      const parts = [(movie && movie.genres) || '', (movie && movie.regions) || '']
        .map(text => String(text).trim())
        .filter(Boolean)
      return parts.join(' · ')
    },
    // 卡片上只放摘要，完整简介留给下方「介绍」区
    shortText(text, limit) {
      const value = String(text || '').trim()
      if (!value) {
        return ''
      }
      return value.length > limit ? value.slice(0, limit) + '…' : value
    },
    reserve (url) {
      url = "https://movie.douban.com/subject/" + url
      this.loading = true
      setTimeout(() => {
            this.loading = false
            window.open(url)
          }
      , 2000)
    },
    getCommentsByDoubanId(id){
      this.tableLoading = true
      // console.log("douban_id", id)
      const form = {"douban_id": id}
      getComments(form).then(res=>{
        // console.log(res.data)
        this.comments = res.data.data
        if(this.comments.length == 0)
          this.$snackbar({content: '暂无影评数据！', top:true, center:true, color:'red',multiLine: true})
        this.tableLoading = false
      })
    }
  },
};
</script>

<style scoped>
</style>
