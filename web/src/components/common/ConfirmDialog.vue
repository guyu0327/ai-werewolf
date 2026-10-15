<script setup lang="ts">
import { watch } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    message: string
    confirmText?: string
    cancelText?: string
    danger?: boolean
  }>(),
  { confirmText: '确定', cancelText: '取消', danger: false },
)

const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: []
}>()

function close() {
  emit('update:modelValue', false)
}

function confirm() {
  // 先发 confirm 再关闭：父组件可能在 confirm 处理器里读取仍在绑定的目标数据
  emit('confirm')
  close()
}

function onKey(e: KeyboardEvent) {
  if (!props.modelValue) return
  if (e.key === 'Escape') close()
  else if (e.key === 'Enter') confirm()
}

watch(
  () => props.modelValue,
  (v) => {
    if (v) window.addEventListener('keydown', onKey)
    else window.removeEventListener('keydown', onKey)
  },
)
</script>

<template>
  <Teleport to="body">
    <Transition name="confirm-fade">
      <div v-if="modelValue" class="confirm-mask" @click.self="close">
        <div class="confirm-box" :class="{ danger }" role="alertdialog" aria-modal="true">
          <div class="confirm-head">
            <span class="confirm-gem" />
            <div class="confirm-title">{{ title }}</div>
          </div>
          <div class="confirm-message">{{ message }}</div>
          <div class="confirm-foot">
            <button class="btn" @click="close">{{ cancelText }}</button>
            <button class="btn" :class="danger ? 'danger' : 'primary'" @click="confirm">{{ confirmText }}</button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped lang="scss">
.confirm-mask {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 16, 0.68);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 320;
}

.confirm-box {
  width: 380px;
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-lg $spacing-xl;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(var(--c-primary-rgb), 0.08);

  &.danger .confirm-gem {
    background: linear-gradient(180deg, #d07a72, rgba(208, 122, 114, 0.35));
    box-shadow: 0 0 10px rgba(208, 122, 114, 0.45);
  }
}

.confirm-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.confirm-gem {
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: linear-gradient(180deg, $night-accent, var(--c-primary));
  box-shadow: 0 0 10px rgba(var(--c-primary-rgb), 0.45);
  flex-shrink: 0;
}

.confirm-title {
  font-size: $font-size-md;
  font-weight: 600;
  color: $text-primary;
}

.confirm-message {
  font-size: $font-size-sm;
  color: $text-secondary;
  line-height: 1.7;
  padding-left: 14px;
}

.confirm-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-left: 14px;

  .btn {
    min-width: 76px;
  }
}

.confirm-fade-enter-active,
.confirm-fade-leave-active {
  transition: opacity 0.18s ease;

  .confirm-box {
    transition: transform 0.18s ease;
  }
}

.confirm-fade-enter-from,
.confirm-fade-leave-to {
  opacity: 0;

  .confirm-box {
    transform: scale(0.96) translateY(6px);
  }
}
</style>
