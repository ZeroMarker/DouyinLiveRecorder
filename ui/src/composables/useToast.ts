import { ref } from 'vue'

export interface ToastState {
  msg: string
  ok: boolean
  visible: boolean
}

const toastState = ref<ToastState>({ msg: '', ok: true, visible: false })
let timer: number | undefined

export function useToast() {
  function toast(msg: string, ok = true) {
    toastState.value = { msg, ok, visible: true }
    clearTimeout(timer)
    timer = setTimeout(() => (toastState.value.visible = false), 3200)
  }
  return { toastState, toast }
}
