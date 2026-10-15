<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 14 })
</script>

<template>
  <svg :width="size" :height="size" viewBox="0 0 64 64" class="sheriff-svg" role="img" aria-label="警长">
    <!-- 绶带 -->
    <polygon points="22,0 42,0 42,22 32,15 22,22" fill="#4E9BE0" />
    <rect x="26" y="0" width="4" height="22" fill="#7FB8EE" />
    <rect x="34" y="0" width="4" height="22" fill="#7FB8EE" />
    <!-- 徽章底盘 -->
    <circle cx="32" cy="40" r="22" fill="#7A5417" />
    <circle cx="32" cy="40" r="19" fill="#F6C64A" />
    <!-- 八芒星 -->
    <polygon
      points="32,23 34.87,33.07 44.02,27.98 38.93,37.13 49,40 38.93,42.87 44.02,52.02 34.87,46.93 32,57 29.13,46.93 19.98,52.02 25.07,42.87 15,40 25.07,37.13 19.98,27.98 29.13,33.07"
      fill="#FBF0CE"
      stroke="#E3AE39"
      stroke-width="1.5"
    />
    <!-- 中心宝石 -->
    <polygon points="32,34 37.5,40 32,46 26.5,40" fill="#EF6B86" />
    <polygon points="32,34 37.5,40 32,40" fill="#F79CAC" />
  </svg>
</template>

<style scoped>
.sheriff-svg {
  display: inline-block;
  vertical-align: -3px;
  filter: drop-shadow(0 0 4px rgba(226, 190, 118, 0.5));
}
</style>
