<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const rows = ref([])
const batchId = ref(null)
const err = ref('')

const RESULT_TAG = {
  applied_upsert: '',
  applied_delete: '',
  skipped_duplicate: 'ok',
  conflict_quarantined: 'warn',
  batch_rejected: 'danger',
}
const RESULT_LABEL = {
  applied_upsert: '写入',
  applied_delete: '删除',
  skipped_duplicate: '跳过重复',
  conflict_quarantined: '冲突隔离',
  batch_rejected: '整批拒绝',
}

async function refresh() {
  const q = new URLSearchParams()
  if (batchId.value) q.set('batch_id', String(batchId.value))
  rows.value = await getJSON('/api/events?' + q.toString())
}

onMounted(async () => {
  try {
    if (route.query.batch_id) batchId.value = Number(route.query.batch_id)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(batchId, async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>事件留痕</h1>
    <p class="muted">
      本批（或全部）已处理事件：写入 / 删除 / 跳过重复（摘要相同，未二次改投影）/ 冲突隔离 / 整批拒绝。
      每条都带载荷摘要，重复事件另附既往摘要供互证。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;gap:0.75rem;align-items:flex-end">
      <label>
        批次 ID（可选）
        <input v-model.number="batchId" type="number" style="display:block;margin-top:0.25rem;width:8rem" placeholder="全部" />
      </label>
      <button @click="batchId = null; refresh()">看全部</button>
    </div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>event_id</th><th>实体键</th><th>op</th><th>seq</th>
            <th>事件时间</th><th>结果</th><th>载荷摘要</th><th>既往摘要</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="e in rows" :key="e.id">
            <td>{{ e.batch_id }}</td>
            <td class="mono">{{ e.event_id }}</td>
            <td class="mono">{{ e.entity_key }}</td>
            <td class="mono">{{ e.op }}</td>
            <td class="mono">{{ e.seq }}</td>
            <td class="mono">{{ e.event_time }}</td>
            <td><span class="tag" :class="RESULT_TAG[e.result]">{{ RESULT_LABEL[e.result] || e.result }}</span></td>
            <td class="mono digest">{{ e.payload_digest ? e.payload_digest.slice(0, 12) : '—' }}</td>
            <td class="mono digest">{{ e.prior_digest ? e.prior_digest.slice(0, 12) : '—' }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="9" class="muted">暂无留痕。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.digest { color: var(--accent-deep); }
</style>
