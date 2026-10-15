<script setup lang="ts">
import { computed } from 'vue'

// 统一使用名字首字 + 哈希配色的文字头像；avatar 参数保留兼容但不渲染
const props = defineProps<{
  name: string
  avatar?: string
  size?: number
}>()

const palette = ['#e8875b', '#5b9bd5', '#70ad47', '#9b6bc9', '#d56aa0', '#4db6ac', '#c9a44a']

const bg = computed(() => {
  let hash = 0
  for (const ch of props.name) hash = (hash * 31 + ch.charCodeAt(0)) | 0
  return palette[Math.abs(hash) % palette.length]
})
</script>

<template>
  <div
    class="avatar"
    :style="{
      width: (size ?? 36) + 'px',
      height: (size ?? 36) + 'px',
      fontSize: (size ?? 36) * 0.42 + 'px',
      background: bg,
    }"
  >
    <span>{{ name.charAt(0) }}</span>
  </div>
</template>

<style scoped lang="scss">
.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: $radius-sm;
  color: $text-white;
  font-weight: 600;
  flex-shrink: 0;
  overflow: hidden;
  user-select: none;
}
</style>
