<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { save } from '@tauri-apps/plugin-dialog'
import { api, isTauri, playUrl } from '../api'
import type { VideoFile } from '../types'
import { useToast } from '../composables/useToast'

const props = defineProps<{ active: boolean }>()
const { toast } = useToast()

const videos = ref<VideoFile[]>([])
const playing = ref(false)
const player = ref<HTMLVideoElement | null>(null)
let timer: number | undefined

const groups = computed(() => {
  const m = new Map<string, VideoFile[]>()
  for (const f of videos.value) {
    const parts = f.path.split('/')
    const key = parts.length > 1 ? parts.slice(0, -1).join('/') : '根目录'
    const list = m.get(key) || []
    list.push(f)
    m.set(key, list)
  }
  return [...m.entries()]
})

async function loadVideos() {
  try {
    const d = await api<{ files: VideoFile[] }>('/api/videos')
    videos.value = (d.files || []).sort((a, b) => (b.mtime || 0) - (a.mtime || 0))
  } catch (e) {
    toast(`加载文件失败: ${(e as Error).message}`, false)
  }
}

function play(path: string) {
  playing.value = true
  const el = player.value
  if (!el) return
  el.pause()
  el.removeAttribute('src')
  el.load()
  el.src = playUrl(path)
  el.play().catch(() => {})
}

async function download(f: VideoFile) {
  try {
    if (isTauri()) {
      const dest = await save({ defaultPath: f.name })
      if (!dest) return
      await invoke('save_file', { path: f.path, dest })
      toast('已保存到 ' + dest)
    } else {
      const a = document.createElement('a')
      a.href = '/videos/' + f.path.split('/').map(encodeURIComponent).join('/')
      a.download = f.name
      a.click()
    }
  } catch (e) {
    toast(`保存失败: ${(e as Error).message}`, false)
  }
}

function fmtSize(n: number) {
  if (!n) return '-'
  const u = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024
    i++
  }
  return v.toFixed(1) + ' ' + u[i]
}

watch(
  () => props.active,
  (on) => {
    clearInterval(timer)
    if (on) {
      loadVideos()
      timer = window.setInterval(loadVideos, 15000)
    }
  },
  { immediate: true },
)
onUnmounted(() => clearInterval(timer))

defineExpose({ play })
</script>

<template>
  <div>
    <div class="form-row">
      <button class="btn" type="button" @click="loadVideos">刷新</button>
      <span class="muted">点击文件名播放 / 下载按钮保存</span>
    </div>
    <div v-if="playing" style="margin-bottom: 12px">
      <video ref="player" controls style="width:100%; max-height:480px; background:#000; border-radius:8px;"></video>
    </div>
    <div id="file-list">
      <div v-if="!videos.length" class="empty">暂无录制文件</div>
      <template v-for="[dir, files] in groups" :key="dir">
        <div style="margin:10px 0 4px;color:var(--accent);font-weight:600;">📁 {{ dir }}</div>
        <div v-for="f in files" :key="f.path" class="file-item">
          <a class="path" href="#" @click.prevent="play(f.path)">{{ f.name }}</a>
          <span class="size">{{ fmtSize(f.size) }}</span>
          <button class="btn sm" type="button" @click="download(f)">下载</button>
        </div>
      </template>
    </div>
  </div>
</template>
