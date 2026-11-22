import { ref } from 'vue'

export interface ToastState {
  text: string
  error: boolean
}

export const toastState = ref<ToastState | null>(null)
let timer: number | undefined

export function notify(text: string, error = false) {
  toastState.value = { text, error }
  window.clearTimeout(timer)
  timer = window.setTimeout(() => (toastState.value = null), 4200)
}

export function notifyError(e: unknown) {
  const msg = e instanceof Error ? e.message : String(e)
  notify(msg, true)
}
