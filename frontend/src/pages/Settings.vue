<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const settings = ref({})
const err = ref('')
onMounted(async () => {
  try { settings.value = await getJSON('/api/settings') } catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">
      全局底座参数只读展示。互斥的重复 event_id 策略（策略甲幂等 / 策略乙整批拒绝，以及策略甲的冲突结局）
      已下沉到每条投影流，在「投影流」页按流配置，开跑时读取并冻结。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td class="mono">{{ k }}</td>
            <td><span class="tag">{{ v }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel">
      <h3 style="margin-top:0">策略口径备忘</h3>
      <ul class="muted" style="line-height:1.8">
        <li>载荷摘要：对载荷做键序无关的规范化 JSON 后取 SHA-256；null 载荷（墓碑删除）有固定摘要。</li>
        <li>策略甲 · 幂等：重复 event_id 摘要相同 → 跳过并计跳过重复，不二次改投影；摘要不同 → 记摘要冲突，按流配置的冲突结局处理（整批失败回滚本趟 / 冲突事件隔离且其余继续，二选一写死贯彻）。</li>
        <li>策略乙 · 整批拒绝：凡重复 event_id 一律整批拒绝，投影回到本趟前，不区分摘要。</li>
        <li>策略在每趟开始时读取快照并随批次留痕；批次进行中改策略不改写本趟结果。</li>
      </ul>
    </div>
  </div>
</template>
