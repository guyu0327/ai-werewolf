import { reactive } from 'vue'

interface ToastItem {
  id: number
  text: string
  leaving: boolean
}

const state = reactive({
  items: [] as ToastItem[],
})

let seq = 0

export function useToastState() {
  return state
}

/** 轻提示：居中上方出现，自动淡出，无需交互 */
export function showToast(text: string, duration = 1600) {
  const item: ToastItem = { id: ++seq, text, leaving: false }
  state.items.push(item)
  if (state.items.length > 4) state.items.splice(0, state.items.length - 4)
  setTimeout(() => {
    item.leaving = true
    setTimeout(() => {
      const i = state.items.indexOf(item)
      if (i >= 0) state.items.splice(i, 1)
    }, 260)
  }, duration)
}
