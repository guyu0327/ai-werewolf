<script setup lang="ts">
import { useToastState } from '@/composables/toast'

const state = useToastState()
</script>

<template>
  <Teleport to="body">
    <div class="toast-host">
      <transition-group name="toast">
        <div v-for="item in state.items" :key="item.id" class="toast" :class="{ leaving: item.leaving }">
          {{ item.text }}
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.toast-host {
  position: fixed;
  top: 56px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 500;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $spacing-sm;
  pointer-events: none;
}

.toast {
  padding: 8px $spacing-xl;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.75);
  color: #fff;
  font-size: $font-size-sm;
  white-space: nowrap;
  box-shadow: $shadow-md;
  transition: opacity 0.26s ease, transform 0.26s ease;
}

.toast.leaving {
  opacity: 0;
  transform: translateY(-8px);
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.22s ease, transform 0.22s ease;
}

.toast-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}

.toast-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}
</style>
