<script setup lang="ts">
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { LogEntry } from '../types'
import { useToast } from '../composables/useToast'

const props = defineProps<{ active: boolean }>()
const { toast } = useToast()

const logs = ref<LogEntry[]>([])
const autoscroll = ref(true)
const hint = ref('')
const box = ref<HTMLDivElement | null>(null)
let timer: number | undefined

async function loadLogs() {
  try {
    const d = await api<{ logs: LogEntry[] }>('/api/logs?limit=300')
    logs.value = d.logs || []
    hint.value = `${logs.value.length} 条 · ${new Date().toLocaleTimeString()}`
    await nextTick()
    const el = box.value
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48
    if (autoscroll.value || nearBottom) el.scrollTop = el.scrollHeight
  } catch (e) {
    toast(`加载日志失败: ${(e as Error).message}`, false)
  }
}

watch(
  () => props.active,
  (on) => {
    clearInterval(timer)
    if (on) {
      loadLogs()
      timer = window.setInterval(loadLogs, 5000)
    }
  },
  { immediate: true },
)
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <div class="form-row">
      <button class="btn" type="button" @click="loadLogs">刷新</button>
      <label class="muted"><input v-model="autoscroll" type="checkbox"> 自动滚动</label>
      <span class="muted">{{ hint }}</span>
    </div>
    <div ref="box" class="logs">
      <div v-if="!logs.length" class="empty">暂无日志</div>
      <div v-for="(l, i) in logs" :key="i" class="row" :class="'log-' + (l.level || 'INFO')">
        [{{ new Date(l.time * 1000).toLocaleTimeString() }}] [{{ l.level }}] {{ l.message }}
      </div>
    </div>
  </div>
</template>
