<script setup lang="ts">
/**
 * Page 13 — administrator analysis settings and metric definitions (WP-D).
 *
 * Read-only metric contract from `GET /api/admin/metrics/definitions`, plus a
 * browser-local view preference (prefix `mi2_admin_pref_`). Nothing here can
 * edit the admin allow-list, database, auth rules, BYOK keys, crawler limits or
 * migration config; those are declared as protected, not configurable.
 */
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'
import {
  PREF_KEYS, PREF_PREFIX, clearPrefs, fmtUtc, loadMetricsDefinitions, parseDays, readPref,
  statusMeta, writePref,
  type Days, type MetricsDefinitions,
} from './opsApi'
import '../admin-ui.css'

const props = withDefaults(defineProps<{days?: Days}>(), {days: 30})
const emit = defineEmits<{days: [days: Days]}>()
const DAY_CHOICES: Days[] = [7, 30, 90]

const data = ref<MetricsDefinitions | null>(null)
const error = ref('')
const busy = ref(false)
let controller: AbortController | undefined

const storedDays = ref<string | null>(readPref(PREF_KEYS.defaultDays))
const storedBasis = ref<string | null>(readPref(PREF_KEYS.preferenceBasis))
const prefDays = ref<Days>(parseDays(storedDays.value ?? props.days))
const prefBasis = ref<'by_user' | 'by_pair'>(storedBasis.value === 'by_pair' ? 'by_pair' : 'by_user')
const savedFlash = ref('')

const CONSTANT_LABELS: Record<string, string> = {
  tmdb_min_votes: 'TMDB 有效投票门槛',
  tmdb_rating_method: 'TMDB 评分方法',
  exposure_retention_days: '曝光/点击记录保留',
  effective_exposure: '有效曝光定义',
  active_user: '活跃账户定义',
  high_rating_threshold: '高评分阈值',
  high_rating_scale: '评分量表',
  interest_min_pairs: '兴趣最小正向对',
  interest_min_impressions: '兴趣最小曝光',
  suppression_min_users: '细分隐藏阈值',
  ai_failure_rate_threshold: 'AI 失败率阈值',
  ai_failure_min_calls: 'AI 判定最小调用',
  recommendation_gap_window_days: '断档判定窗口',
  recommendation_gap_stale_hours: '断档判定最久间隔',
  recommendation_gap_min_impressions: '断档判定最小曝光',
  windows: '允许的统计窗口',
  timezone: '统计时区',
}
const UNIT_LABELS: Record<string, string> = {
  movie: '部', account: '账户', event: '条事件', pair: '对', ratio: '比率',
  score: '分', call: '次调用', token: 'Token', impression: '次曝光', click: '次点击',
  day: '天', status: '状态', ms: '毫秒',
}
const PROTECTED = [
  {key: 'admins', title: '管理员白名单', reason: '由服务端环境变量 MI2_ADMIN_USERS 控制，修改需重新部署。'},
  {key: 'db', title: '数据库连接', reason: '连接串与凭据只存在于服务端环境，页面不读取也不回显。'},
  {key: 'auth', title: '认证与权限规则', reason: '登录、CSRF、会话与管理员鉴权由后端实现，不提供在线开关。'},
  {key: 'byok', title: 'BYOK 加密密钥', reason: '用户模型 Key 的加密主密钥独立保存，管理员页面不可见、不可导出。'},
  {key: 'crawl', title: '采集速率与安全上限', reason: '限速、合规与重试上限属于采集配置，需受控发布。'},
  {key: 'migration', title: '生产迁移配置', reason: '真实 DDL/DML 与数据迁移必须走单独授权的受控流程。'},
]

async function load() {
  const current = new AbortController()
  controller?.abort()
  controller = current
  busy.value = true
  error.value = ''
  try {
    data.value = await loadMetricsDefinitions(current.signal)
  } catch (e) {
    if (current.signal.aborted) return
    error.value = (e as Error).message
  } finally {
    if (!current.signal.aborted) busy.value = false
  }
}

onMounted(load)
onBeforeUnmount(() => controller?.abort())
defineExpose({refresh: load})

function constantValue(key: string, value: unknown): string {
  if (value == null) return '—'
  if (key === 'tmdb_rating_method') return value === 'simple_mean' ? '简单平均（每片等权）' : String(value)
  if (key === 'timezone') return String(value)
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') {
    if (key === 'ai_failure_rate_threshold' && value <= 1) return (value * 100).toFixed(0) + '%'
    if (key.endsWith('_days')) return value + ' 天'
    if (key.endsWith('_hours')) return value + ' 小时'
    return String(value)
  }
  if (Array.isArray(value)) return value.join(' / ')
  return String(value)
}

const constantRows = computed(() => {
  const constants = data.value?.constants || {}
  return Object.keys(constants)
    .filter(key => key !== 'windows' && key !== 'timezone')
    .map(key => ({key, label: CONSTANT_LABELS[key] || key, value: constantValue(key, constants[key])}))
})

const definitions = computed(() => data.value?.definitions || [])
const statusOfData = computed(() => statusMeta(data.value?.status))

function saveDays(value: Days) {
  prefDays.value = value
  storedDays.value = String(value)
  writePref(PREF_KEYS.defaultDays, String(value))
  savedFlash.value = '已保存默认窗口：近 ' + value + ' 天'
  emit('days', value)
}
function saveBasis(value: 'by_user' | 'by_pair') {
  prefBasis.value = value
  storedBasis.value = value
  writePref(PREF_KEYS.preferenceBasis, value)
  savedFlash.value = '已保存默认偏好口径：' + (value === 'by_user' ? '按用户去重' : '按用户-电影对')
}
function restoreDefaults() {
  clearPrefs()
  storedDays.value = null
  storedBasis.value = null
  prefDays.value = 30
  prefBasis.value = 'by_user'
  savedFlash.value = '已清除本浏览器的视图偏好，并回到默认 30 天窗口。'
  emit('days', 30)
}
</script>

<template>
  <section class="adm-panel ops-settings" aria-labelledby="admin-settings-title">
    <h2 id="admin-settings-title">全局设置与统计口径
      <small v-if="data">指标版本 {{ data.version }} · {{ data.timezone }}<template v-if="data.as_of"> · 生成于 {{ fmtUtc(data.as_of) }}</template></small>
    </h2>

    <p v-if="error" class="adm-errobox" role="alert">{{ error }}
      <button type="button" class="adm-btn ops-inline-btn" @click="load">重新读取</button>
    </p>
    <p v-else-if="busy && !data" class="adm-state" role="status">正在读取指标口径…</p>

    <template v-if="data">
      <div class="ops-facts">
        <div><span>指标版本</span><b>{{ data.version }}</b></div>
        <div><span>统计时区</span><b>{{ data.timezone }}</b></div>
        <div><span>允许窗口</span><b>{{ (data.windows || []).map(day => day + ' 天').join(' / ') || '—' }}</b></div>
        <div><span>接口状态</span><b><span class="ops-status" :class="'tone-' + statusOfData.tone">{{ statusOfData.label }}</span></b></div>
      </div>

      <h3>关键常量 <small>与后端实现同源，只读</small></h3>
      <div class="adm-tablewrap">
        <table>
          <caption class="ops-sr">统计口径关键常量</caption>
          <thead><tr><th scope="col">常量</th><th scope="col">取值</th></tr></thead>
          <tbody>
            <tr v-for="row in constantRows" :key="row.key">
              <th scope="row">{{ row.label }}<small class="ops-key">{{ row.key }}</small></th>
              <td class="ops-value">{{ row.value }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3>口径清单 <small>{{ definitions.length }} 条 · 前端只读展示</small></h3>
      <div class="adm-tablewrap">
        <table class="ops-def-table">
          <caption class="ops-sr">指标定义、来源、单位与状态</caption>
          <thead>
            <tr><th scope="col">指标</th><th scope="col">口径</th><th scope="col">来源</th><th scope="col">单位</th><th scope="col">状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in definitions" :key="row.key">
              <th scope="row">{{ row.title }}<small class="ops-key">{{ row.key }}</small></th>
              <td>{{ row.definition }}</td>
              <td class="ops-source">{{ row.source || '—' }}</td>
              <td>{{ UNIT_LABELS[row.unit || ''] || row.unit || '—' }}</td>
              <td><span class="ops-status" :class="'tone-' + statusMeta(row.status).tone">{{ statusMeta(row.status).label }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-for="(note, index) in data.notes || []" :key="index" class="adm-note">{{ note }}</p>
    </template>

    <h3>管理员客户端视图偏好 <small>仅存本浏览器 · 前缀 {{ PREF_PREFIX }}</small></h3>
    <p class="adm-warnbox ops-pref-warning" role="note">
      仅保存在当前浏览器本地（localStorage），<b>不影响其他管理员、不修改全站算法、不写入数据库</b>；
      清理浏览器数据或点击“恢复默认”即可移除。
    </p>
    <div class="adm-toolbar">
      <div class="adm-field">
        <span>默认统计窗口</span>
        <div class="adm-seg" role="group" aria-label="默认统计窗口">
          <button v-for="option in DAY_CHOICES" :key="option" type="button"
                  :aria-pressed="prefDays === option" @click="saveDays(option)">近 {{ option }} 天</button>
        </div>
      </div>
      <div class="adm-field">
        <span>默认偏好口径</span>
        <div class="adm-seg" role="group" aria-label="默认偏好口径">
          <button type="button" :aria-pressed="prefBasis === 'by_user'" @click="saveBasis('by_user')">按用户去重</button>
          <button type="button" :aria-pressed="prefBasis === 'by_pair'" @click="saveBasis('by_pair')">按用户-电影对</button>
        </div>
      </div>
      <div class="adm-actions ops-toolbar-actions">
        <button type="button" class="adm-btn" :disabled="!storedDays && !storedBasis" @click="restoreDefaults">恢复默认</button>
      </div>
    </div>
    <p class="ops-pref-state" role="status">
      <span v-if="storedDays || storedBasis">已保存：
        <template v-if="storedDays">默认窗口近 {{ storedDays }} 天</template>
        <template v-if="storedDays && storedBasis"> · </template>
        <template v-if="storedBasis">默认偏好口径 {{ storedBasis === 'by_pair' ? '按用户-电影对' : '按用户去重' }}</template>
      </span>
      <span v-else>当前浏览器尚未保存视图偏好，使用系统默认（近 30 天 / 按用户去重）。</span>
      <span v-if="savedFlash" class="ops-saved">{{ savedFlash }}</span>
    </p>
    <p class="adm-note">
      修改默认窗口会同步切换当前驾驶舱的统计窗口；带 <code>?days=</code> 的链接仍以链接参数为准，偏好不会覆盖共享链接。
      偏好读取自本浏览器的 <code>{{ PREF_PREFIX }}default_days</code> 与 <code>{{ PREF_PREFIX }}preference_basis</code>。
    </p>

    <h3>受保护配置 <small>以下配置不提供在线编辑入口</small></h3>
    <ul class="ops-protected">
      <li v-for="item in PROTECTED" :key="item.key">
        <span class="ops-lock" aria-hidden="true">只读</span>
        <div><b>{{ item.title }}</b><p>{{ item.reason }}</p></div>
      </li>
    </ul>
    <p class="adm-note">
      本页只读展示统计口径；如需调整管理员名单、数据库连接、认证规则、BYOK 密钥、采集速率或迁移配置，
      必须按受控运维流程单独授权，不在驾驶舱内执行。
    </p>
  </section>
</template>

<style scoped>
.ops-inline-btn{margin-left:10px;padding:3px 9px;font-size:11px}
.ops-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px 16px}
.ops-facts div{display:flex;align-items:baseline;justify-content:space-between;gap:12px;padding:9px 12px;border:1px solid rgba(241,236,226,.10);border-radius:9px;background:#191a1f;font-size:11.5px}
.ops-facts span{color:#a9a294}
.ops-facts b{color:#f0eadf;font-weight:500;text-align:right;font-variant-numeric:tabular-nums}
.ops-status{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(241,236,226,.16);border-radius:999px;padding:2px 8px;font-size:10.5px;color:#a9a294;white-space:nowrap}
.ops-status::before{content:"";width:5px;height:5px;border-radius:50%;background:currentColor;opacity:.9}
.ops-status.tone-ok{color:#a9c4ae;border-color:rgba(156,191,166,.32)}
.ops-status.tone-warn{color:#dcc49b;border-color:rgba(200,164,106,.32)}
.ops-status.tone-bad{color:#e0a79c;border-color:rgba(232,160,148,.34)}
.ops-status.tone-muted{color:#a9a294}
.ops-key{display:block;margin-top:3px;color:#7e796f;font-size:10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:400}
.ops-value{font-variant-numeric:tabular-nums;color:#efe9de}
.ops-source{color:#a9a294;font-size:11px;max-width:220px}
.ops-def-table th[scope="row"]{color:#efe9de;font-weight:500;white-space:nowrap}
.ops-pref-warning{margin-bottom:12px}
.ops-toolbar-actions{margin:0}
.ops-pref-state{display:flex;flex-wrap:wrap;gap:6px 16px;margin:10px 0 0;color:#a9a294;font-size:11px}
.ops-saved{color:#cbb38f}
.ops-protected{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.ops-protected li{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:10px;align-items:start;border:1px solid rgba(241,236,226,.10);border-radius:9px;background:#191a1f;padding:11px 13px}
.ops-lock{border:1px solid rgba(241,236,226,.18);border-radius:4px;color:#8f8a80;font-size:9.5px;letter-spacing:.06em;padding:1px 5px;margin-top:2px}
.ops-protected b{font-size:11.5px;font-weight:500;color:#e9e3d7}
.ops-protected p{margin:4px 0 0;color:#8f8a80;font-size:10.5px;line-height:1.65}
.ops-sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
/* Legibility floor: functional micro-text stays at 11px or above. */
.ops-settings :is(.ops-status, .ops-lock, .ops-key, .ops-source, .ops-pref-state, .ops-protected p){font-size:11px}
.ops-settings .ops-facts div{font-size:12px}
.ops-settings .ops-protected b{font-size:12px}
.ops-settings .ops-protected p{font-size:11.5px}
.ops-settings .ops-source{font-size:11.5px}
.ops-settings .ops-key{color:#8f8a80}
.ops-settings :is(button,summary):focus-visible{outline:2px solid #c8a46a;outline-offset:2px}
@media (max-width: 1100px){
  .ops-protected{grid-template-columns:minmax(0,1fr)}
}
</style>