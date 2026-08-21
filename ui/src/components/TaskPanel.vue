<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api } from '../api'
import type { PlatformInfo, TaskItem, TaskStatus } from '../types'
import { useToast } from '../composables/useToast'

const props = defineProps<{ active: boolean }>()
const emit = defineEmits<{ play: [path: string] }>()
const { toast } = useToast()

const tasks = ref<TaskItem[]>([])
const platforms = ref<PlatformInfo[]>([])
const mode = ref<'platform' | 'url'>('platform')
const platform = ref('')
const id = ref('')
const url = ref('')
const quality = ref('')
const name = ref('')
const search = ref('')
const filter = ref('all')
const busy = ref(false)
let timer: number | undefined

const STATUS_LABEL: Record<string, string> = {
  recording: '录制中', waiting: '等待检测', offline: '未开播',
  error: '出错', stopped: '已暂停', unknown: '未知',
}

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return tasks.value.filter((t) => {
    const st = statusOf(t)
    const statusMatch = filter.value === 'all' || st === filter.value ||
      (filter.value === 'waiting' && ['waiting', 'offline', 'unknown'].includes(st))
    const haystack = [t.anchor, t.name, t.platform, t.url].join(' ').toLowerCase()
    return statusMatch && (!q || haystack.includes(q))
  })
})

const idPlaceholder = computed(() => {
  const p = platforms.value.find((x) => x.name === platform.value)
  return p?.id_placeholder || '直播间ID / 用户名'
})

function statusOf(t: TaskItem): TaskStatus {
  return t.commented ? 'stopped' : (t.status || 'unknown')
}

async function loadTasks() {
  try {
    const d = await api<{ tasks: TaskItem[] }>('/api/tasks')
    tasks.value = d.tasks || []
  } catch (e) {
    toast(`加载任务失败: ${(e as Error).message}`, false)
  }
}

async function loadPlatforms() {
  try {
    const d = await api<{ platforms: PlatformInfo[] }>('/api/platforms')
    platforms.value = d.platforms.filter((p) => p.url_template)
  } catch (e) {
    toast(`加载平台列表失败: ${(e as Error).message}`, false)
  }
}

async function addTask() {
  if (busy.value) return
  busy.value = true
  try {
    if (mode.value === 'platform') {
      if (!platform.value) return toast('请选择平台', false)
      if (!id.value.trim()) return toast('请输入直播间ID/用户名', false)
      await api('/api/tasks/from-id', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform: platform.value, id: id.value.trim(), quality: quality.value, name: name.value.trim() }),
      })
    } else {
      if (!url.value.trim()) return toast('请输入 URL', false)
      await api('/api/tasks', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.value.trim(), quality: quality.value, name: name.value.trim() }),
      })
    }
    toast('任务已添加')
    id.value = ''
    url.value = ''
    await loadTasks()
  } catch (e) {
    toast(`添加失败: ${(e as Error).message}`, false)
  } finally {
    busy.value = false
  }
}

async function removeTask(t: TaskItem) {
  if (!confirm(`确认删除该任务？\n${t.url}`)) return
  try {
    await api(`/api/tasks?url=${encodeURIComponent(t.url)}`, { method: 'DELETE' })
    toast('已删除')
    await loadTasks()
  } catch (e) {
    toast(`删除失败: ${(e as Error).message}`, false)
  }
}

async function toggleTask(t: TaskItem, commented: boolean) {
  try {
    await api(`/api/tasks/comment?url=${encodeURIComponent(t.url)}&commented=${commented}`, { method: 'PUT' })
    await loadTasks()
  } catch (e) {
    toast(`操作失败: ${(e as Error).message}`, false)
  }
}

function play(path: string) {
  emit('play', path)
}

function shortUrl(u: string) {
  return u.length > 48 ? u.slice(0, 48) + '…' : u
}

function fmtDur(sec: number) {
  sec = Math.floor(sec || 0)
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return (h ? h + 'h ' : '') + (h || m ? m + 'm ' : '') + s + 's'
}

watch(
  () => props.active,
  (on) => {
    clearInterval(timer)
    if (on) {
      loadTasks()
      loadPlatforms()
      timer = window.setInterval(loadTasks, 5000)
    }
  },
  { immediate: true },
)
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div>
    <form class="form-row" @submit.prevent="addTask">
      <select v-model="mode" aria-label="添加方式">
        <option value="platform">🎯 选平台 + 输入ID</option>
        <option value="url">🔗 粘贴完整网址</option>
      </select>
      <select v-if="mode === 'platform'" v-model="platform" style="min-width:150px" aria-label="直播平台">
        <option value="">-- 选择平台 --</option>
        <option v-for="p in platforms" :key="p.name" :value="p.name">{{ p.name }}</option>
      </select>
      <input v-if="mode === 'platform'" v-model="id" type="text" :placeholder="idPlaceholder" style="width:240px" aria-label="直播间 ID 或用户名">
      <input v-else v-model="url" type="url" placeholder="直播间 URL，如 https://live.douyin.com/123456" aria-label="直播间 URL">
      <select v-model="quality" aria-label="录制画质">
        <option value="">默认画质</option>
        <option v-for="q in ['原画', '蓝光', '超清', '高清', '标清', '流畅']" :key="q">{{ q }}</option>
      </select>
      <input v-model="name" type="text" placeholder="备注（可选）" style="width:120px" aria-label="任务备注">
      <button class="btn primary" type="submit" :disabled="busy">{{ busy ? '处理中…' : '添加任务' }}</button>
    </form>

    <div class="toolbar">
      <input v-model="search" type="search" placeholder="搜索主播、平台或 URL" aria-label="搜索任务">
      <select v-model="filter" aria-label="筛选任务状态">
        <option value="all">全部状态</option>
        <option value="recording">录制中</option>
        <option value="waiting">等待/未开播</option>
        <option value="stopped">已暂停</option>
        <option value="error">出错</option>
      </select>
      <button class="btn" type="button" @click="loadTasks">刷新</button>
      <span class="summary">显示 {{ filtered.length }} / {{ tasks.length }} 个任务</span>
    </div>

    <div class="table-scroll">
      <table>
        <thead><tr><th>状态</th><th>主播</th><th>平台</th><th>画质</th><th>已录制</th><th>URL</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-if="!filtered.length">
            <td colspan="7" class="empty">{{ tasks.length ? '没有符合筛选条件的任务' : '暂无任务，请在上方添加直播间 URL' }}</td>
          </tr>
          <tr v-for="t in filtered" :key="t.url">
            <td data-label="状态">
              <span class="badge" :class="'b-' + statusOf(t)">{{ t.commented ? '已暂停' : (STATUS_LABEL[statusOf(t)] || '未知') }}</span>
            </td>
            <td data-label="主播">
              {{ t.anchor || t.name || '-' }}
              <div v-if="t.recording_seconds" class="muted">{{ fmtDur(t.recording_seconds) }}</div>
            </td>
            <td data-label="平台">{{ t.platform || '-' }}</td>
            <td data-label="画质">{{ t.quality }}</td>
            <td data-label="已录制" class="muted">{{ t.recording_seconds ? fmtDur(t.recording_seconds) : '-' }}</td>
            <td data-label="URL"><span class="muted" :title="t.url">{{ shortUrl(t.url) }}</span></td>
            <td data-label="操作">
              <button class="btn sm" type="button" @click="toggleTask(t, !t.commented)">{{ t.commented ? '恢复' : '暂停' }}</button>
              <button class="btn sm danger" type="button" @click="removeTask(t)">删除</button>
              <button v-if="t.file" class="btn sm" type="button" @click="play(t.file)">播放</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
