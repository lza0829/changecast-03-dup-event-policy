<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const err = ref('')

const POLICY_LABELS = {
  idempotent: '甲·幂等',
  reject_batch: '乙·整批拒绝',
}
onMounted(async () => {
  try { rows.value = await getJSON('/api/batches?limit=100') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>应用批次</h1>
    <p class="muted">
      每趟投影落一批次并冻结当时策略：源事件、写入/删除、跳过重复、摘要冲突、隔离与整批拒绝计数。
      被拒绝的批次投影零变更，可与投影快照互证。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>批次</th><th>投影流</th><th>状态</th><th>冻结策略</th><th>拒绝原因</th>
            <th>源事件</th><th>写入</th><th>删除</th><th>跳过</th><th>冲突</th><th>隔离</th><th>拒绝</th>
            <th>存活</th><th>已删</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td class="mono">{{ b.batch_no }}</td>
            <td>{{ b.stream_title }}</td>
            <td><span class="tag" :class="b.status === 'rejected' ? 'danger' : 'ok'">{{ b.status }}</span></td>
            <td class="mono" style="font-size:0.78rem">{{ POLICY_LABELS[b.duplicate_policy] || '—' }}</td>
            <td class="mono" style="font-size:0.78rem">{{ b.reject_reason || '—' }}</td>
            <td>{{ b.source_events }}</td>
            <td>{{ b.applied_upsert }}</td>
            <td>{{ b.applied_delete }}</td>
            <td>{{ b.skipped_duplicate }}</td>
            <td>{{ b.payload_conflicts }}</td>
            <td>{{ b.quarantined_events }}</td>
            <td>{{ b.batch_rejections }}</td>
            <td>{{ b.projected_alive }}</td>
            <td>{{ b.projected_deleted }}</td>
            <td class="mono">{{ b.created_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="15" class="muted">尚无批次。</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
