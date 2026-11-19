<script setup lang="ts">
/**
 * Page 12 — data collection and operation audit (WP-D).
 *
 * Three read-only sections: TMDB supplement batches (`WP-A §3.3`), the user
 * behaviour event feed (`WP-B §7`) and the existing import audit component.
 * Counts that the backend cannot derive stay “—” with `missing_fields`; event
 * payloads are never returned by the API and are never requested here.
 */
import {onBeforeUnmount, onMounted, reactive, ref} from 'vue'
import AdminImportAudit from '../../AdminImportAudit.vue'
import {
  BATCH_STATUSES, EM_DASH, EVENT_KINDS, KIND_LABELS, fmtInt, fmtUtc, fmtUtcNaive,
  loadBatches, loadEvents, statusMeta,
  type BatchItem, type BatchesPage, type Days, type EventKind, type EventsPage, type EventRow,
} from './opsApi'
import '../admin-ui.css'

// Accepted for the shared cockpit contract; batches/events use explicit date ranges.
withDefaults(defineProps<{days?: Days}>(), {days: 30})

const emit = defineEmits<{'open-movie': [id: number]}>()

const BATCH_STATUS_LABELS: Record<string, string> = {
  pending: '等待中', running: '执行中', succeeded: '成功', partial: '部分成功', failed: '失败',
}
const ACTION_LABELS: Record<string, string> = {set: '设置', unset: '撤销'}
const BATCH_LIMIT = 10
const EVENT_LIMIT = 20

const batches = ref<BatchesPage | null>(null)
const batchesError = ref('')
const batchesBusy = ref(false)
const batchFilters = reactive({status: '', from: '', to: ''})
const batchOffset = ref(0)

const events = ref<EventsPage | null>(null)
const eventsError = ref('')
const eventsBusy = ref(false)
const eventFilters = reactive({from: '', to: '', kind: '' as EventKind | '', movieId: ''})
const eventOffset = ref(0)

const importAudit = ref<InstanceType<typeof AdminImportAudit>>()
let batchController: AbortController | undefined
let eventController: AbortController | undefined

function dayStart(value: string): string | null {
  return value ? value + 'T00:00:00Z' : null
}
/** Event/batch windows are left-closed right-open, so `to` includes the picked day. */
function dayAfter(value: string): string | null {
  if (!value) return null
  const date = new Date(value + 'T00:00:00Z')
  if (Number.isNaN(date.getTime())) return null
  date.setUTCDate(date.getUTCDate() + 1)
  return date.toISOString().slice(0, 19) + 'Z'
}

async function loadBatchPage() {
  const current = new AbortController()
  batchController?.abort()
  batchController = current
  batchesBusy.value = true
  batchesError.value = ''
  try {
    batches.value = await loadBatches({
      limit: BATCH_LIMIT,
      offset: batchOffset.value,
      status: batchFilters.status || null,
      startedFrom: dayStart(batchFilters.from),
      startedTo: dayAfter(batchFilters.to),
    }, current.signal)
  } catch (e) {
    if (current.signal.aborted) return
    batchesError.value = (e as Error).message
  } finally {
    if (!current.signal.aborted) batchesBusy.value = false
  }
}

async function loadEventPage() {
  const rawMovie = String(eventFilters.movieId ?? '').trim()
  if (rawMovie && !/^\d+$/.test(rawMovie)) {
    eventsError.value = '影片 ID 需要是正整数。'
    return
  }
  const current = new AbortController()
  eventController?.abort()
  eventController = current
  eventsBusy.value = true
  eventsError.value = ''
  try {
    events.value = await loadEvents({
      limit: EVENT_LIMIT,
      offset: eventOffset.value,
      from: dayStart(eventFilters.from),
      to: dayAfter(eventFilters.to),
      kind: eventFilters.kind || null,
      movieId: rawMovie ? Number(rawMovie) : null,
    }, current.signal)
  } catch (e) {
    if (current.signal.aborted) return
    eventsError.value = (e as Error).message
  } finally {
    if (!current.signal.aborted) eventsBusy.value = false
  }
}

function applyBatchFilters() {
  batchOffset.value = 0
  loadBatchPage()
}
function resetBatchFilters() {
  batchFilters.status = ''
  batchFilters.from = ''
  batchFilters.to = ''
  batchOffset.value = 0
  loadBatchPage()
}
function applyEventFilters() {
  eventOffset.value = 0
  loadEventPage()
}
function resetEventFilters() {
  eventFilters.from = ''
  eventFilters.to = ''
  eventFilters.kind = ''
  eventFilters.movieId = ''
  eventOffset.value = 0
  loadEventPage()
}
function stepBatches(delta: number) {
  const next = batchOffset.value + delta
  if (next < 0 || next >= (batches.value?.total || 0)) return
  batchOffset.value = next
  loadBatchPage()
}
function stepEvents(delta: number) {
  const next = eventOffset.value + delta
  if (next < 0 || next >= (events.value?.total || 0)) return
  eventOffset.value = next
  loadEventPage()
}

function refresh() {
  loadBatchPage()
  loadEventPage()
  importAudit.value?.refresh()
}

onMounted(() => {
  loadBatchPage()
  loadEventPage()
})
onBeforeUnmount(() => {
  batchController?.abort()
  eventController?.abort()
})
defineExpose({refresh})

function batchCounts(row: BatchItem) {
  const counts = row.reported_counts || {}
  const pick = (key: string) => typeof counts[key] === 'number' ? counts[key] as number : null
  return {
    target: pick('target_documents'),
    success: pick('success_count'),
    failed: pick('failed_count'),
    skipped: pick('skipped_count'),
  }
}
function itemSummary(row: BatchItem): string {
  const item = row.item_status
  if (!item) return EM_DASH
  const parts = [
    ['新增', item.inserted], ['更新', item.updated], ['跳过', item.skipped],
    ['复核', item.review], ['错误', item.error],
  ].filter(([, value]) => typeof value === 'number') as [string, number][]
  if (!parts.length) return EM_DASH
  return parts.map(([label, value]) => label + ' ' + value).join(' · ')
}
function openMovie(row: EventRow) {
  if (typeof row.movie_id === 'number' && Number.isInteger(row.movie_id) && row.movie_id > 0) {
    emit('open-movie', row.movie_id)
  }
}
const BATCH_CHOICES = BATCH_STATUSES.map(status => ({value: status, label: BATCH_STATUS_LABELS[status] || status}))
</script>

<template>
  <section class="adm-panel ops-audit" aria-labelledby="admin-audit-title">
    <h2 id="admin-audit-title">操作审计 <small>只读 · 批次、事件与导入记录</small></h2>

    <h3>TMDB 补采批次 <small>计数只取自 counts_json 与实际入库行，缺键显示“—”</small></h3>
    <div class="adm-toolbar">
      <label class="adm-field">
        <span>批次状态</span>
        <select v-model="batchFilters.status" @change="applyBatchFilters">
          <option value="">全部状态</option>
          <option v-for="item in BATCH_CHOICES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label class="adm-field">
        <span>开始时间 ≥</span>
        <input v-model="batchFilters.from" type="date" @change="applyBatchFilters">
      </label>
      <label class="adm-field">
        <span>开始时间 ≤</span>
        <input v-model="batchFilters.to" type="date" @change="applyBatchFilters">
      </label>
      <div class="adm-actions ops-toolbar-actions">
        <button type="button" class="adm-btn" @click="resetBatchFilters">清除筛选</button>
        <button type="button" class="adm-btn" :disabled="batchesBusy" @click="applyBatchFilters">查询</button>
      </div>
    </div>

    <p v-if="batchesError" class="adm-errobox" role="alert">{{ batchesError }}
      <button type="button" class="adm-btn ops-inline-btn" @click="loadBatchPage">重新读取</button>
    </p>
    <p v-else-if="batchesBusy && !batches" class="adm-state" role="status">正在读取补采批次…</p>
    <template v-else-if="batches">
      <p v-if="!batches.items.length" class="adm-empty">该过滤条件下没有补采批次记录。</p>
      <template v-else>
        <div class="adm-tablewrap">
          <table class="ops-batch-table">
            <caption class="ops-sr">TMDB 补采批次（只读）</caption>
            <thead>
              <tr>
                <th scope="col">批次</th><th scope="col">模式</th><th scope="col">状态</th>
                <th scope="col">开始</th><th scope="col">结束</th>
                <th scope="col" class="num">目标 / 成功 / 失败 / 跳过</th>
                <th scope="col">入库明细</th><th scope="col">错误码</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in batches.items" :key="row.batch_id">
                <th scope="row">#{{ row.batch_id }}</th>
                <td>{{ row.mode || EM_DASH }}</td>
                <td><span class="ops-status" :class="'tone-' + statusMeta(row.status).tone">{{ BATCH_STATUS_LABELS[row.status] || row.status }}</span></td>
                <td>{{ fmtUtc(row.started_at) }}</td>
                <td>{{ fmtUtc(row.finished_at) }}</td>
                <td class="num">
                  {{ fmtInt(batchCounts(row).target) }} / {{ fmtInt(batchCounts(row).success) }} /
                  {{ fmtInt(batchCounts(row).failed) }} / {{ fmtInt(batchCounts(row).skipped) }}
                  <small v-if="row.missing_fields?.length" class="ops-missing">缺键：{{ row.missing_fields.join('、') }}</small>
                </td>
                <td>{{ itemSummary(row) }}</td>
                <td>
                  <template v-if="row.error_codes?.length">
                    <span v-for="error in row.error_codes.slice(0, 3)" :key="error.error_code" class="ops-code">{{ error.error_code }} ×{{ error.count }}</span>
                    <small v-if="row.error_codes.length > 3" class="ops-missing">另有 {{ row.error_codes.length - 3 }} 类</small>
                  </template>
                  <span v-else>{{ EM_DASH }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="adm-pager">
          <button type="button" class="adm-btn" :disabled="batchOffset === 0 || batchesBusy" @click="stepBatches(-BATCH_LIMIT)">上一页</button>
          <span>第 {{ batchOffset + 1 }}–{{ batchOffset + batches.items.length }} 条 / 共 {{ fmtInt(batches.total) }} 个批次</span>
          <button type="button" class="adm-btn" :disabled="!batches.has_more || batchesBusy" @click="stepBatches(BATCH_LIMIT)">下一页</button>
        </div>
      </template>
      <p class="adm-note">计数仅来自 <code>counts_json</code> 与 <code>ingest_items</code> 实际行；历史批次缺键时保持“—”，不推断目标数。本页只读，不触发补采。</p>
    </template>

    <h3>用户行为事件 <small>不返回事件载荷、评分明细或私人内容</small></h3>
    <div class="adm-toolbar">
      <label class="adm-field">
        <span>起始日期</span>
        <input v-model="eventFilters.from" type="date" @change="applyEventFilters">
      </label>
      <label class="adm-field">
        <span>结束日期</span>
        <input v-model="eventFilters.to" type="date" @change="applyEventFilters">
      </label>
      <label class="adm-field">
        <span>行为类型</span>
        <select v-model="eventFilters.kind" @change="applyEventFilters">
          <option value="">全部类型</option>
          <option v-for="item in EVENT_KINDS" :key="item" :value="item">{{ KIND_LABELS[item] }}</option>
        </select>
      </label>
      <label class="adm-field">
        <span>影片 ID</span>
        <input v-model="eventFilters.movieId" type="number" min="1" step="1" inputmode="numeric" placeholder="可选" @keyup.enter="applyEventFilters">
      </label>
      <div class="adm-actions ops-toolbar-actions">
        <button type="button" class="adm-btn" @click="resetEventFilters">清除筛选</button>
        <button type="button" class="adm-btn" :disabled="eventsBusy" @click="applyEventFilters">查询</button>
      </div>
    </div>

    <p v-if="eventsError" class="adm-errobox" role="alert">{{ eventsError }}
      <button type="button" class="adm-btn ops-inline-btn" @click="loadEventPage">重新读取</button>
    </p>
    <p v-else-if="eventsBusy && !events" class="adm-state" role="status">正在读取行为事件…</p>
    <template v-else-if="events">
      <p v-if="!events.items.length" class="adm-empty">该过滤条件下没有行为事件。</p>
      <template v-else>
        <div class="adm-tablewrap">
          <table>
            <caption class="ops-sr">用户行为事件（只读，UTC）</caption>
            <thead>
              <tr><th scope="col">时间（UTC）</th><th scope="col">账号</th><th scope="col">影片</th><th scope="col">行为</th><th scope="col">动作</th></tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in events.items" :key="index">
                <td>{{ fmtUtcNaive(row.at) }}</td>
                <td>{{ row.user }}</td>
                <td>
                  <button v-if="row.movie_id" type="button" class="ops-link" :title="row.title || ''" @click="openMovie(row)">
                    {{ row.title || '影片 #' + row.movie_id }}
                  </button>
                  <span v-else>{{ row.title || EM_DASH }}</span>
                </td>
                <td>{{ KIND_LABELS[row.kind || ''] || row.kind || EM_DASH }}</td>
                <td>{{ ACTION_LABELS[row.action || ''] || row.action || EM_DASH }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="adm-pager">
          <button type="button" class="adm-btn" :disabled="eventOffset === 0 || eventsBusy" @click="stepEvents(-EVENT_LIMIT)">上一页</button>
          <span>第 {{ eventOffset + 1 }}–{{ eventOffset + events.items.length }} 条 / 共 {{ fmtInt(events.total) }} 条事件</span>
          <button type="button" class="adm-btn" :disabled="!events.has_more || eventsBusy" @click="stepEvents(EVENT_LIMIT)">下一页</button>
        </div>
      </template>
      <p class="adm-note">
        服务端按事件序号倒序返回，仅包含时间、账号、影片与事件类型；点击片名进入电影详情。
        {{ events.definition || '不含事件 payload。' }}
      </p>
    </template>

    <div class="ops-embed">
      <AdminImportAudit ref="importAudit"/>
    </div>
    <p class="adm-note">所有审计查询只读；任何修复或补采行为都由受控维护流程产生记录，不在本页执行。</p>
  </section>
</template>

<style scoped>
.ops-toolbar-actions{margin:0 0 2px}
.ops-inline-btn{margin-left:10px;padding:3px 9px;font-size:11px}
.ops-status{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(241,236,226,.16);border-radius:999px;padding:2px 8px;font-size:10.5px;color:#a9a294;white-space:nowrap}
.ops-status::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.9}
.ops-status.tone-ok{color:#a9c4ae;border-color:rgba(156,191,166,.32)}
.ops-status.tone-warn{color:#dcc49b;border-color:rgba(200,164,106,.32)}
.ops-status.tone-bad{color:#e0a79c;border-color:rgba(232,160,148,.34)}
.ops-status.tone-muted{color:#a9a294}
.ops-missing{display:block;margin-top:4px;color:#8f8a80;font-size:10px;line-height:1.5}
.ops-code{display:inline-block;margin:0 6px 4px 0;padding:1px 6px;border:1px solid rgba(232,160,148,.3);border-radius:4px;color:#e0b1a7;font-size:10.5px}
.ops-batch-table{min-width:900px}
.ops-link{border:0;background:transparent;padding:0;font:inherit;color:#e6dfd3;text-align:left;cursor:pointer;text-underline-offset:3px}
.ops-link:hover{color:#d9c7f3;text-decoration:underline}
.ops-embed{margin-top:26px;border-top:1px solid rgba(241,236,226,.10);padding-top:18px}
.ops-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-audit :is(.ops-status, .ops-missing, .ops-code, .ops-inline-btn){font-size:11px}
.ops-audit :is(button,input,select,summary):focus-visible{outline:2px solid #c8a46a;outline-offset:2px}
</style>