<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { apiText } from '../api'
import { useToast } from '../composables/useToast'

const props = defineProps<{ active: boolean }>()
const { toast } = useToast()

const text = ref('')
const original = ref('')
const dirty = ref(false)
const loaded = ref(false)
const saving = ref(false)

function setDirty(v: boolean) {
  dirty.value = v
}

async function loadConfig(force = false) {
  if (dirty.value && !force && !confirm('当前配置尚未保存，确认重新载入吗？')) return
  try {
    original.value = await apiText('/api/config')
    text.value = original.value
    setDirty(false)
    loaded.value = true
  } catch (e) {
    toast(`加载配置失败: ${(e as Error).message}`, false)
  }
}

async function saveConfig() {
  if (saving.value) return
  saving.value = true
  try {
    await apiText('/api/config', { method: 'PUT', headers: { 'Content-Type': 'text/plain' }, body: text.value })
    original.value = text.value
    setDirty(false)
    toast('配置已保存，新开任务自动生效')
  } catch (e) {
    toast(`保存失败: ${(e as Error).message}`, false)
  } finally {
    saving.value = false
  }
}

watch(
  () => props.active,
  (on) => {
    if (on && !loaded.value) loadConfig()
  },
)

function onInput() {
  setDirty(text.value !== original.value)
}

onMounted(() => {
  if (props.active && !loaded.value) loadConfig()
})
</script>

<template>
  <div>
    <div class="form-row">
      <button class="btn primary" type="button" :disabled="saving" @click="saveConfig">{{ saving ? '处理中…' : '保存配置' }}</button>
      <button class="btn" type="button" @click="loadConfig()">重新载入</button>
      <span class="muted" :class="{ dirty }">
        {{ dirty ? '有未保存的更改' : '保存后无需重启：录制进程每轮值守自动重读配置' }}
      </span>
    </div>
    <textarea v-model="text" spellcheck="false" @input="onInput"></textarea>
  </div>
</template>
