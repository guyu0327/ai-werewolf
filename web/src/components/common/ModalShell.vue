<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'

defineProps<{ title?: string; width?: string; height?: string }>()
const emit = defineEmits<{ close: [] }>()

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
onMounted(() => window.addEventListener('keydown', onKey))
onUnmounted(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <Teleport to="body">
    <div class="modal-shell-mask" @click.self="emit('close')">
      <div class="modal-shell" :style="{ width: width ?? '860px', height: height ?? '86vh' }">
        <div class="modal-shell-head">
          <slot name="head">
            <h2 class="modal-shell-title">{{ title }}</h2>
            <slot name="head-extra" />
          </slot>
          <button class="modal-shell-close" title="关闭（Esc）" @click="emit('close')">✕</button>
        </div>
        <div class="modal-shell-body">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.modal-shell-mask {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 16, 0.68);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 300;
}

.modal-shell {
  max-width: calc(100vw - 48px);
  max-height: calc(100vh - 48px);
  background: $bg-base;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(var(--c-primary-rgb), 0.08);
}

.modal-shell-head {
  display: flex;
  align-items: center;
  gap: $spacing-md;
  padding: $spacing-md $spacing-xl;
  border-bottom: 1px solid $border-color;
  background: $bg-sidebar;
  flex-shrink: 0;
}

.modal-shell-title {
  font-size: $font-size-lg;
  color: $text-primary;
}

.modal-shell-close {
  margin-left: auto;
  width: 28px;
  height: 28px;
  border-radius: $radius-md;
  color: $text-secondary;
  cursor: pointer;
  font-size: 14px;
  display: flex;
  align-items: center;
  justify-content: center;

  &:hover {
    background: $bg-hover;
    color: $text-primary;
  }
}

// 滚动策略交给内容方：内容根元素自带 flex:1 + overflow
.modal-shell-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
</style>
