<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const streams = ref([])
const batches = ref([])
const streamId = ref(null)
const note = ref('')
const last = ref(null)
const busy = ref(false)
const err = ref('')

const POLICY_LABELS = {
  idempotent: '策略甲 · 幂等',
  reject_batch: '策略乙 · 整批拒绝',
}
const OUTCOME_LABELS = {
  rollback_batch: '冲突→整批失败回滚本趟',
  quarantine: '冲突→隔离事件，其余继续',
}

const currentStream = computed(() => streams.value.find((s) => s.id === Number(streamId.value)))

async function refresh() {
  streams.value = await getJSON('/api/streams')
  batches.value = await getJSON('/api/batches?limit=20')
  if (streamId.value == null && streams.value.length) streamId.value = streams.value[0].id
}

async function run() {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/project', {
      stream_id: Number(streamId.value),
      note: note.value || null,
    })
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function statusTag(status) {
  return { ok: status === 'completed', danger: status === 'rejected', warn: false }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>投影台</h1>
    <p class="muted">
      选一条投影流，读取 CDC 夹具并按 seq 应用到投影表。开跑入口读取该流的重复 event_id 策略并在本趟冻结；
      重复 event_id 先比对载荷摘要（SHA-256，键序无关）：相同则幂等跳过，不同则记冲突并按配置结局处理。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3 style="margin-top:0">发起投影</h3>
      <div style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
        <label>
          投影流
          <select v-model.number="streamId" style="display:block;min-width:18rem;margin-top:0.25rem">
            <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.title }}</option>
          </select>
        </label>
        <label>
          备注（可选）
          <input v-model="note" style="display:block;width:16rem;margin-top:0.25rem" placeholder="本趟说明" />
        </label>
        <button :disabled="busy || !streamId" @click="run">{{ busy ? '投影中…' : '应用变更流' }}</button>
      </div>
      <div v-if="currentStream" class="policy-snapshot">
        本趟将按 <strong>{{ POLICY_LABELS[currentStream.duplicate_policy] }}</strong>
        <template v-if="currentStream.duplicate_policy === 'idempotent'">
          （{{ OUTCOME_LABELS[currentStream.conflict_outcome] }}）
        </template>
        执行；策略可在「投影流」页切换。
      </div>
    </div>

    <div class="panel" v-if="last">
      <h3 style="margin-top:0">最近一趟</h3>
      <p>
        批次 <span class="mono">{{ last.batch_no }}</span> · {{ last.stream_title }} ·
        <span class="tag" :class="statusTag(last.status)">{{ last.status }}</span>
        <span v-if="last.status === 'rejected'" class="tag danger" style="margin-left:0.5rem">
          拒绝原因：{{ last.reject_reason }}
          <template v-if="last.rejected_event_id">（{{ last.rejected_event_id }}）</template>
        </span>
      </p>
      <p class="muted mono" style="font-size:0.82rem">
        本趟冻结策略：{{ POLICY_LABELS[last.duplicate_policy] }}
        <template v-if="last.duplicate_policy === 'idempotent'">/ {{ OUTCOME_LABELS[last.conflict_outcome] }}</template>
      </p>
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ last.source_events }}</div><div class="stat-label">源事件</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_upsert }}</div><div class="stat-label">写入</div></div>
        <div class="stat"><div class="stat-num">{{ last.applied_delete }}</div><div class="stat-label">删除</div></div>
        <div class="stat stat-skip"><div class="stat-num">{{ last.skipped_duplicate }}</div><div class="stat-label">跳过重复</div></div>
        <div class="stat stat-conflict"><div class="stat-num">{{ last.payload_conflicts }}</div><div class="stat-label">摘要冲突</div></div>
        <div class="stat stat-quar"><div class="stat-num">{{ last.quarantined_events }}</div><div class="stat-label">隔离事件</div></div>
        <div class="stat stat-reject"><div class="stat-num">{{ last.batch_rejections }}</div><div class="stat-label">整批拒绝</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_alive }}</div><div class="stat-label">存活</div></div>
        <div class="stat"><div class="stat-num">{{ last.projected_deleted }}</div><div class="stat-label">已删</div></div>
      </div>
      <p v-if="last.status === 'rejected'" class="tag danger" style="margin-top:0.75rem">
        本趟投影未发生任何变更：投影保持本趟前快照，水位不前移，仅留下批次与事件留痕。
      </p>
      <p v-else-if="last.quarantined_events" class="tag warn" style="margin-top:0.75rem">
        有 {{ last.quarantined_events }} 个冲突事件被隔离、未进投影；本趟其余事件照常应用，无半截成功冒充完成。
      </p>

      <div v-if="last.conflict_preview && last.conflict_preview.length" class="conflict-box">
        <h4 style="margin:0.6rem 0 0.3rem">冲突留痕（双摘要互证）</h4>
        <table>
          <thead>
            <tr><th>event_id</th><th>seq</th><th>本次摘要</th><th>既往摘要</th><th>结局</th></tr>
          </thead>
          <tbody>
            <tr v-for="c in last.conflict_preview" :key="c.id">
              <td class="mono">{{ c.event_id }}</td>
              <td class="mono">{{ c.seq }}</td>
              <td class="mono">{{ c.payload_digest.slice(0, 12) }}</td>
              <td class="mono">{{ c.prior_digest.slice(0, 12) }}</td>
              <td><span class="tag" :class="c.outcome === 'quarantine' ? 'warn' : 'danger'">{{ c.outcome }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>

      <p style="margin-top:0.75rem">
        <router-link :to="{ path: '/projections', query: { stream_id: last.stream_id } }">查看投影快照 →</router-link>
        ·
        <router-link :to="{ path: '/events', query: { batch_id: last.id } }">事件留痕 →</router-link>
        ·
        <router-link :to="{ path: '/conflicts', query: { batch_id: last.id } }">冲突隔离台 →</router-link>
      </p>
    </div>

    <div class="panel">
      <h3 style="margin-top:0">历史批次</h3>
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>状态</th><th>冻结策略</th>
            <th>源事件</th><th>跳过</th><th>冲突</th><th>隔离</th><th>拒绝</th><th>存活</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in batches" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td><span class="tag" :class="statusTag(b.status)">{{ b.status }}</span></td>
            <td class="mono" style="font-size:0.78rem">
              {{ POLICY_LABELS[b.duplicate_policy] || '—' }}
            </td>
            <td>{{ b.source_events }}</td>
            <td>{{ b.skipped_duplicate }}</td>
            <td>{{ b.payload_conflicts }}</td>
            <td>{{ b.quarantined_events }}</td>
            <td>{{ b.batch_rejections }}</td>
            <td>{{ b.projected_alive }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!batches.length"><td colspan="11" class="muted">还没有批次，先跑一趟。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.policy-snapshot {
  margin-top: 0.7rem; padding: 0.5rem 0.7rem;
  background: rgba(196, 92, 38, 0.08);
  border-left: 3px solid var(--accent);
  font-size: 0.88rem;
}
.stat-skip { box-shadow: inset 0 -3px 0 var(--ok); }
.stat-conflict { box-shadow: inset 0 -3px 0 var(--warn); }
.stat-quar { box-shadow: inset 0 -3px 0 var(--warn); }
.stat-reject { box-shadow: inset 0 -3px 0 var(--danger); }
.conflict-box { margin-top: 0.6rem; border: 1px solid var(--line); padding: 0.4rem 0.6rem; }
</style>
