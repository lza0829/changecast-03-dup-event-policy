<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const stats = ref(null)
const err = ref('')
onMounted(async () => {
  try { stats.value = await getJSON('/api/dashboard') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>总览</h1>
    <p class="muted">变更日志投影：源 CDC 事件按序落到当前投影，批次计数、冲突留痕与快照三方互证。</p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="stats">
      <div class="stat-grid">
        <div class="stat"><div class="stat-num">{{ stats.stream_count }}</div><div class="stat-label">投影流</div></div>
        <div class="stat"><div class="stat-num">{{ stats.enabled_streams }}</div><div class="stat-label">已启用</div></div>
        <div class="stat"><div class="stat-num">{{ stats.batch_count }}</div><div class="stat-label">应用批次</div></div>
        <div class="stat stat-reject"><div class="stat-num">{{ stats.rejected_batches }}</div><div class="stat-label">整批拒绝次数</div></div>
        <div class="stat stat-skip"><div class="stat-num">{{ stats.skipped_duplicates }}</div><div class="stat-label">跳过重复</div></div>
        <div class="stat stat-conflict"><div class="stat-num">{{ stats.payload_conflicts }}</div><div class="stat-label">摘要冲突</div></div>
        <div class="stat stat-quar"><div class="stat-num">{{ stats.quarantined_events }}</div><div class="stat-label">隔离事件</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_alive }}</div><div class="stat-label">存活投影</div></div>
        <div class="stat"><div class="stat-num">{{ stats.projection_deleted }}</div><div class="stat-label">已删投影</div></div>
        <div class="stat"><div class="stat-num">{{ stats.ooo_buffered }}</div><div class="stat-label">缓冲中</div></div>
      </div>
      <p style="margin-top:1rem">默认应用序 <span class="tag">{{ stats.apply_order }}</span>（按事件 seq 升序）</p>
      <p>
        <router-link to="/run">去投影台跑一趟 →</router-link>
        ·
        <router-link to="/conflicts">冲突隔离台 →</router-link>
      </p>
    </div>
  </div>
</template>

<style scoped>
.stat-skip { box-shadow: inset 0 -3px 0 var(--ok); }
.stat-conflict { box-shadow: inset 0 -3px 0 var(--warn); }
.stat-quar { box-shadow: inset 0 -3px 0 var(--warn); }
.stat-reject { box-shadow: inset 0 -3px 0 var(--danger); }
</style>
