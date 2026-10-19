<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/common/Avatar.vue'
import { ROLE_LABELS } from '@/utils/feed'

const props = defineProps<{
  seat: number
  name: string
  avatar: string
  alive: boolean
  role: string | null
}>()

const roleLabel = computed(() => (props.role ? ROLE_LABELS[props.role] ?? props.role : '未知'))
const roleClass = computed(() => {
  if (props.role === 'werewolf') return 'wolf'
  if (props.role === 'villager') return 'villager'
  return 'god'
})
</script>

<template>
  <div class="role-card" :class="roleClass">
    <Avatar :name="name" :avatar="avatar" :size="30" />
    <div class="role-info">
      <div class="role-line">
        <span class="role-label">{{ roleLabel }}</span>
        <span v-if="!alive" class="dead-tag">出局</span>
      </div>
      <div class="seat-line">{{ seat }}号 · {{ name }}</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.role-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: $radius-md;
  background: linear-gradient(135deg, rgba(255, 255, 255, 0.03), transparent 50%), $bg-panel;
  border: 1px solid $border-color;
  box-shadow: $shadow-sm;

  &.wolf {
    border-color: rgba(208, 122, 114, 0.5);
    background: linear-gradient(135deg, $wolf-color-light, transparent 60%), $bg-panel;

    .role-label {
      color: #eba3a0;
    }
  }

  &.god {
    border-color: rgba(122, 165, 216, 0.5);
    background: linear-gradient(135deg, $god-color-light, transparent 60%), $bg-panel;

    .role-label {
      color: #aac8ec;
    }
  }

  &.villager {
    .role-label {
      color: #a8c8a8;
    }
  }
}

.role-info {
  line-height: 1.3;
}

.role-line {
  display: flex;
  align-items: center;
  gap: 6px;
}

.role-label {
  font-weight: 700;
  font-size: $font-size-base;
}

.dead-tag {
  font-size: 10px;
  color: $text-tertiary;
  border: 1px solid $border-color;
  border-radius: 4px;
  padding: 0 4px;
}

.seat-line {
  font-size: $font-size-xs;
  color: $text-secondary;
}
</style>
