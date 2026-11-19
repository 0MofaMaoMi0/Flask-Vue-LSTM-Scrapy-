<script setup lang="ts">
import {onBeforeUnmount, onMounted, ref, watch} from 'vue'
import {fmtScore, loadPoster} from './analytics'
import './admin-ui.css'

const props = defineProps<{
  movie: { movie_id: number; title: string; release_year: number | null; vote_average: number | null; vote_count?: number | null }
  rank?: number
  note?: string
}>()
const emit = defineEmits<{open: [movieId: number]}>()
const poster = ref<string | null>(null), failed = ref(false)
let serial = 0, unmounted = false
async function load() {
  const targetId = props.movie.movie_id
  const ticket = ++serial
  poster.value = null
  failed.value = false
  const url = await loadPoster(targetId)
  if (unmounted || ticket !== serial || targetId !== props.movie.movie_id) return
  poster.value = url
  failed.value = !url
}
onMounted(load)
watch(() => props.movie.movie_id, load)
onBeforeUnmount(() => { unmounted = true; ++serial })
</script>

<template>
  <article class="adm-movie">
    <button type="button" class="adm-movie-open" :aria-label="'查看电影详情：' + movie.title" @click="emit('open', movie.movie_id)">
      <div class="adm-movie-poster">
        <img v-if="poster" :src="poster" :alt="movie.title + ' 海报'" loading="lazy" @error="poster = null; failed = true">
        <template v-else>
          <span class="fallback" aria-hidden="true">{{ movie.title.slice(0, 1) }}</span>
          <span class="poster-note">{{ failed ? '海报暂不可用' : '海报读取中' }}</span>
        </template>
        <span v-if="rank" class="poster-rank">{{ String(rank).padStart(2, '0') }}</span>
      </div>
      <div class="adm-movie-meta">
        <strong :title="movie.title">{{ movie.title }}</strong>
        <span>{{ movie.release_year || '年份未知' }} · <b>{{ fmtScore(movie.vote_average) }}</b> 分
          <template v-if="movie.vote_count != null"> · {{ movie.vote_count }} 票</template>
        </span>
        <span v-if="note" class="card-note">{{ note }}</span>
      </div>
    </button>
  </article>
</template>

<style scoped>
.poster-note{position:absolute;bottom:8px;left:8px;right:8px;font-size:10px;line-height:1.4;color:#a096a9;text-align:center}
.poster-rank{position:absolute;top:6px;left:6px;font-size:10px;letter-spacing:.08em;color:#e2ccA8;background:#141519cc;border:1px solid #c8a46a55;border-radius:4px;padding:1px 5px}
.card-note{color:#9e8fae!important;font-size:10px!important;line-height:1.5}
</style>