<script setup>
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { getJSON } from '../api'

const route = useRoute()
const rows = ref([])
const streams = ref([])
const streamId = ref(null)
const batchId = ref(null)
const err = ref('')

async function refresh() {
  const q = new URLSearchParams()
  if (streamId.value) q.set('stream_id', String(streamId.value))
  if (batchId.value) q.set('batch_id', String(batchId.value))
  rows.value = await getJSON('/api/conflicts?' + q.toString())
}

onMounted(async () => {
  try {
    streams.value = await getJSON('/api/streams')
    if (route.query.stream_id) streamId.value = Number(route.query.stream_id)
    if (route.query.batch_id) batchId.value = Number(route.query.batch_id)
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch([streamId, batchId], async () => {
  try { await refresh() } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>冲突隔离台</h1>
    <p class="muted">
      同一 event_id 再次出现且载荷摘要不同时在此留痕。隔离（quarantine）的事件不进投影、本趟其余事件继续；
      整批回滚（batch_rejected）表示该趟投影已回到本趟前。两条摘要并列可互证冲突属实。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" style="display:flex;flex-wrap:wrap;gap:0.75rem;align-items:flex-end">
      <label>
        投影流
        <select v-model="streamId" style="display:block;min-width:14rem;margin-top:0.25rem">
          <option :value="null">全部</option>
          <option v-for="s in streams" :key="s.id" :value="s.id">{{ s.title }}</option>
        </select>
      </label>
      <label>
        批次 ID（可选）
        <input v-model.number="batchId" type="number" style="display:block;margin-top:0.25rem;width:8rem" placeholder="全部" />
      </label>
      <button @click="streamId = null; batchId = null; refresh()">看全部</button>
    </div>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>流</th><th>event_id</th><th>实体键</th><th>seq</th>
            <th>本次载荷</th><th>本次摘要</th><th>既往摘要</th><th>冲突类型</th><th>结局</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in rows" :key="c.id">
            <td class="mono">{{ c.batch_id }}</td>
            <td>{{ c.stream_title }}</td>
            <td class="mono">{{ c.event_id }}</td>
            <td class="mono">{{ c.entity_key }}</td>
            <td class="mono">{{ c.seq }}</td>
            <td class="mono">{{ c.payload == null ? '—' : JSON.stringify(c.payload) }}</td>
            <td class="mono digest">{{ c.payload_digest.slice(0, 12) }}</td>
            <td class="mono digest">{{ c.prior_digest.slice(0, 12) }}</td>
            <td><span class="tag warn">{{ c.conflict_kind }}</span></td>
            <td>
              <span class="tag" :class="c.outcome === 'quarantine' ? 'warn' : 'danger'">
                {{ c.outcome === 'quarantine' ? '已隔离' : '整批拒绝' }}
              </span>
            </td>
            <td class="mono">{{ c.created_at }}</td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="11" class="muted">暂无冲突。相同载荷的重复只会幂等跳过，不会出现在这里。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.digest { color: var(--accent-deep); }
</style>
