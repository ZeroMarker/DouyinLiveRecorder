<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref } from 'vue'
import { api, initApi } from './api'
import type { StatusInfo } from './types'
import { useToast } from './composables/useToast'
import TaskPanel from './components/TaskPanel.vue'
import ConfigPanel from './components/ConfigPanel.vue'
import LogsPanel from './components/LogsPanel.vue'
import FilesPanel from './components/FilesPanel.vue'

const { toastState } = useToast()

const status = ref<StatusInfo | null>(null)
const connected = ref(false)
const lastUpdate = ref('')
const activeTab = ref<'tasks' | 'config' | 'logs' | 'files'>('tasks')
const filesRef = ref<InstanceType<typeof FilesPanel> | null>(null)
let timer: number | undefined

async function loadStatus() {
  try {
    status.value = await api<StatusInfo>('/api/status')
    connected.value = true
    lastUpdate.value = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    connected.value = false
  }
}

function startPolling() {
  loadStatus()
  timer = window.setInterval(() => {
    if (document.hidden) return
    loadStatus()
  }, 5000)
}

async function ensureBackend() {
  while (!(await initApi())) await new Promise((r) => setTimeout(r, 2000))
  startPolling()
}

function handlePlay(path: string) {
  activeTab.value = 'files'
  nextTick(() => filesRef.value?.play(path))
}

onMounted(ensureBackend)
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <header>
    <h1>📹 DouyinLiveRecorder</h1>
    <span class="ver">{{ status?.version || '' }}</span>
    <span class="spacer"></span>
    <span class="connection" :class="connected ? 'online' : 'offline'">{{ connected ? '服务正常' : '连接失败' }}</span>
    <span class="ver" v-if="lastUpdate">更新于 {{ lastUpdate }}</span>
  </header>

  <div class="cards">
    <div class="card"><div class="num">{{ status?.recording_count ?? '-' }}</div><div class="lbl">录制中</div></div>
    <div class="card"><div class="num">{{ status?.task_count ?? '-' }}</div><div class="lbl">任务数</div></div>
    <div class="card"><div class="num">{{ status?.disk_free_gb ?? '-' }}</div><div class="lbl">磁盘剩余 (GB)</div></div>
    <div class="card"><div class="num">{{ status?.platform_count ?? '-' }}</div><div class="lbl">支持平台</div></div>
  </div>

  <nav class="tabs" role="tablist" aria-label="管理功能">
    <button v-for="tab in ([['tasks', '任务管理'], ['config', '配置'], ['logs', '日志'], ['files', '录制文件']] as const)"
      :key="tab[0]" :class="{ active: activeTab === tab[0] }" role="tab" :aria-selected="activeTab === tab[0]"
      @click="activeTab = tab[0]">{{ tab[1] }}</button>
  </nav>

  <div class="panel" :class="{ active: activeTab === 'tasks' }">
    <TaskPanel :active="activeTab === 'tasks'" @play="handlePlay" />
  </div>
  <div class="panel" :class="{ active: activeTab === 'config' }">
    <ConfigPanel :active="activeTab === 'config'" />
  </div>
  <div class="panel" :class="{ active: activeTab === 'logs' }">
    <LogsPanel :active="activeTab === 'logs'" />
  </div>
  <div class="panel" :class="{ active: activeTab === 'files' }">
    <FilesPanel ref="filesRef" :active="activeTab === 'files'" />
  </div>

  <div v-if="toastState.visible" class="toast" :class="toastState.ok ? 'ok' : 'err'" role="status" aria-live="polite">
    {{ toastState.msg }}
  </div>
</template>
