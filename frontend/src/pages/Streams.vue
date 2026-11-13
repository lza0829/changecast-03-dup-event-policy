<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, putJSON } from '../api'

const rows = ref([])
const err = ref('')
const savingId = ref(null)
const saveMsg = ref('')
// Per-stream working copy of the policy form, keyed by stream id.
const draft = ref({})

const POLICY_LABELS = {
  idempotent: '策略甲 · 幂等',
  reject_batch: '策略乙 · 整批拒绝',
}
const OUTCOME_LABELS = {
  rollback_batch: '整批失败回滚本趟',
  quarantine: '冲突事件隔离，其余继续',
}

async function refresh() {
  rows.value = await getJSON('/api/streams')
  for (const s of rows.value) {
    if (!draft.value[s.id]) {
      draft.value[s.id] = {
        duplicate_policy: s.duplicate_policy,
        conflict_outcome: s.conflict_outcome,
      }
    }
  }
}

async function savePolicy(s) {
  err.value = ''
  saveMsg.value = ''
  const d = draft.value[s.id]
  savingId.value = s.id
  try {
    const updated = await putJSON(`/api/streams/${s.id}/policy`, {
      duplicate_policy: d.duplicate_policy,
      conflict_outcome: d.conflict_outcome,
    })
    s.duplicate_policy = updated.duplicate_policy
    s.conflict_outcome = updated.conflict_outcome
    saveMsg.value = `已保存：${s.title}（下一趟生效；进行中的批次不受影响）`
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    savingId.value = null
  }
}

onMounted(async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>投影流</h1>
    <p class="muted">
      每条流绑定 CDC 夹具、实体键名与载荷字段，并配置互斥的重复 event_id 策略。
      策略在一趟开始时读取并冻结，批次进行中改策略不会改写本趟结果。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="saveMsg" class="tag ok">{{ saveMsg }}</p>

    <div class="panel" v-for="s in rows" :key="s.id">
      <div class="stream-head">
        <h3 style="margin:0">{{ s.title }} <span class="mono muted">({{ s.code }})</span></h3>
        <span class="tag" :class="s.enabled ? 'ok' : 'danger'">{{ s.enabled ? '启用' : '停用' }}</span>
      </div>
      <table class="meta-table">
        <tbody>
          <tr><th>业务域 / 实体键</th><td class="mono">{{ s.domain }} · {{ s.entity_key_name }}</td></tr>
          <tr><th>载荷字段</th><td class="mono">{{ s.payload_fields.join(', ') }}</td></tr>
          <tr><th>夹具 / 水位 seq</th><td class="mono">{{ s.fixture }} · {{ s.watermark_seq }}</td></tr>
        </tbody>
      </table>

      <div class="policy-box" v-if="draft[s.id]">
        <div class="policy-row">
          <span class="policy-key">重复 event_id 策略（互斥二选一）</span>
          <label class="radio">
            <input type="radio" :name="`policy-${s.id}`" value="idempotent"
                   v-model="draft[s.id].duplicate_policy" />
            <span><strong>策略甲 · 幂等</strong>：摘要相同→跳过并计跳过重复，不二次改投影；
              摘要不同→记冲突，按右侧冲突结局处理</span>
          </label>
          <label class="radio">
            <input type="radio" :name="`policy-${s.id}`" value="reject_batch"
                   v-model="draft[s.id].duplicate_policy" />
            <span><strong>策略乙 · 整批拒绝</strong>：凡重复 event_id 一律整批拒绝，
              投影回到本趟前，不区分摘要</span>
          </label>
        </div>
        <div class="policy-row" v-if="draft[s.id].duplicate_policy === 'idempotent'">
          <span class="policy-key">摘要不同时的冲突结局（写死贯彻）</span>
          <label class="radio">
            <input type="radio" :name="`outcome-${s.id}`" value="rollback_batch"
                   v-model="draft[s.id].conflict_outcome" />
            <span>{{ OUTCOME_LABELS.rollback_batch }}</span>
          </label>
          <label class="radio">
            <input type="radio" :name="`outcome-${s.id}`" value="quarantine"
                   v-model="draft[s.id].conflict_outcome" />
            <span>{{ OUTCOME_LABELS.quarantine }}</span>
          </label>
        </div>
        <p class="muted policy-current" v-if="draft[s.id].duplicate_policy === 'reject_batch'">
          策略乙不看载荷摘要，冲突结局选项对本趟不生效。
        </p>
        <div class="policy-actions">
          <button :disabled="savingId === s.id" @click="savePolicy(s)">
            {{ savingId === s.id ? '保存中…' : '保存策略' }}
          </button>
          <span class="muted">当前库内：{{ POLICY_LABELS[s.duplicate_policy] }}
            <template v-if="s.duplicate_policy === 'idempotent'">/ {{ OUTCOME_LABELS[s.conflict_outcome] }}</template>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.stream-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.6rem; }
.meta-table th { width: 11rem; text-transform: none; letter-spacing: 0; font-size: 0.85rem; }
.policy-box { margin-top: 0.75rem; padding-top: 0.75rem; border-top: 1px dashed var(--line); }
.policy-row { margin-bottom: 0.6rem; }
.policy-key {
  display: block; font-weight: 700; font-size: 0.88rem;
  color: var(--accent-deep); margin-bottom: 0.35rem;
}
.radio { display: flex; gap: 0.45rem; align-items: flex-start; margin: 0.3rem 0; font-size: 0.9rem; }
.radio input { margin-top: 0.2rem; }
.policy-current { font-size: 0.82rem; margin: 0.3rem 0; }
.policy-actions { display: flex; align-items: center; gap: 0.8rem; margin-top: 0.5rem; }
</style>
