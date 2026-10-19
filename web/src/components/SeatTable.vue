<script setup lang="ts">
import { computed } from 'vue'
import Avatar from '@/components/common/Avatar.vue'
import SheriffBadge from '@/components/common/SheriffBadge.vue'
import type { PublicPlayer } from '@/api/game'

const props = withDefaults(
  defineProps<{
    players: PublicPlayer[]
    mySeat: number | null
    activeSeat?: number | null
    selectableSeats?: number[] | null
    selectedSeat?: number | null
    phase?: string
    sheriffSeat?: number | null
  }>(),
  { activeSeat: null, selectableSeats: null, selectedSeat: null, phase: '', sheriffSeat: null },
)

const emit = defineEmits<{ select: [seat: number] }>()

interface Placed extends PublicPlayer {
  x: number
  y: number
}

const placed = computed<Placed[]>(() => {
  const n = props.players.length
  return props.players.map((p) => {
    // 座位沿椭圆排布：真人（视角持有者）固定在正下方 6 点钟位置；观战局按座位号顺时针排
    const idx = props.players.findIndex((q) => q.seat === p.seat)
    const myIdx = props.players.findIndex((q) => q.seat === props.mySeat)
    const rel = myIdx >= 0 ? (idx - myIdx + n) % n : idx
    const angle = (Math.PI / 2) + (rel / n) * Math.PI * 2
    return {
      ...p,
      x: 50 + 42 * Math.cos(angle),
      y: 50 + 36 * Math.sin(angle),
    }
  })
})

const selectable = computed(() => new Set(props.selectableSeats ?? []))

const isNight = computed(() => props.phase.startsWith('night'))

function onClick(p: Placed) {
  if (selectable.value.has(p.seat)) emit('select', p.seat)
}

function roleClass(role: string | null): string {
  if (role === 'werewolf') return 'wolf'
  if (role === 'villager') return 'villager'
  return 'god'
}
</script>

<template>
  <div class="seat-table" :class="{ night: isNight }">
    <div class="table-oval">
      <div class="table-center">
        <slot name="center"></slot>
      </div>
    </div>
    <div
      v-for="p in placed"
      :key="p.seat"
      class="seat"
      :class="{
        dead: !p.alive,
        me: p.seat === mySeat,
        speaking: p.seat === activeSeat,
        selectable: selectable.has(p.seat),
        selected: p.seat === selectedSeat,
        sheriff: p.seat === sheriffSeat,
      }"
      :style="{ left: p.x + '%', top: p.y + '%' }"
      @click="onClick(p)"
    >
      <div class="seat-no">{{ p.seat }}号</div>
      <Avatar :name="p.name" :avatar="p.avatar" :size="46" />
      <div class="seat-name" :class="{ 'is-me': p.is_human }">{{ p.name }}<span v-if="p.is_human" class="me-tag">你</span></div>
      <div class="seat-tags">
        <div v-if="p.role_label" class="seat-role" :class="roleClass(p.role)">
          {{ p.role_label }}<span v-if="p.vote_lost" class="vote-lost-tag">失投票权</span>
        </div>
        <span v-if="p.seat === sheriffSeat" class="sheriff-badge" title="警长">
          <SheriffBadge :size="13" />警长
        </span>
      </div>
      <div v-if="p.seat === selectedSeat" class="selected-ring"></div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.seat-table {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 380px;
}

.table-oval {
  position: absolute;
  left: 14%;
  right: 14%;
  top: 18%;
  bottom: 18%;
  border: 1px solid rgba(var(--c-primary-rgb), 0.22);
  border-radius: 50%;
  background:
    radial-gradient(ellipse at center, rgba(var(--c-primary-rgb), 0.07), transparent 68%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(0, 0, 0, 0.25));
  box-shadow:
    inset 0 0 60px rgba(0, 0, 0, 0.45),
    0 0 40px rgba(0, 0, 0, 0.3);
  transition: background $transition-normal;
}

// 夜晚：桌面沉入更深的夜色，边框转为冷紫
.seat-table.night .table-oval {
  border-color: rgba(122, 165, 216, 0.16);
  background:
    radial-gradient(ellipse at center, rgba(122, 165, 216, 0.05), transparent 68%),
    linear-gradient(180deg, rgba(0, 0, 0, 0.1), rgba(0, 0, 0, 0.45));
}

.table-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  pointer-events: none;
}

.seat {
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 6px 8px;
  border-radius: $radius-lg;
  transition: opacity $transition-normal, filter $transition-normal, box-shadow $transition-fast,
    background $transition-fast;
  user-select: none;

  &.dead {
    // 出局：头像置灰、名字弱化，但保留角色徽章彩色可读
    .avatar {
      filter: grayscale(1) brightness(0.7);
    }

    .seat-name {
      color: $text-secondary;
    }
  }

  &.speaking {
    animation: speak-glow 1.6s ease-in-out infinite;

    :deep(.avatar) {
      box-shadow: 0 0 14px rgba(var(--c-primary-rgb), 0.45);
    }
  }

  &.selectable {
    cursor: pointer;
    animation: pick-breathe 1.4s ease-in-out infinite;

    &:hover {
      background: $bg-panel-hover;
    }
  }

  &.selected {
    box-shadow: 0 0 0 2px $wolf-color;
    background: $wolf-color-light;
  }

  // 警长：头像描金环，全场一眼可辨
  &.sheriff :deep(.avatar) {
    box-shadow: 0 0 0 2px rgba(226, 190, 118, 0.85), 0 0 12px rgba(226, 190, 118, 0.35);
  }
}

@keyframes speak-glow {
  0%,
  100% {
    box-shadow: 0 0 0 2px rgba(var(--c-primary-rgb), 0.9);
  }

  50% {
    box-shadow: 0 0 0 2px rgba(var(--c-primary-rgb), 0.35), 0 0 18px rgba(var(--c-primary-rgb), 0.3);
  }
}

@keyframes pick-breathe {
  0%,
  100% {
    box-shadow: 0 0 0 1px rgba(var(--c-primary-rgb), 0.55);
  }

  50% {
    box-shadow: 0 0 0 2px rgba(var(--c-primary-rgb), 0.95);
  }
}

.seat-no {
  font-size: $font-size-xs;
  color: $text-secondary;
}

.sheriff-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10px;
  line-height: 16px;
  padding: 0 6px;
  border-radius: 999px;
  font-weight: 600;
  color: #f0d48a;
  background: linear-gradient(135deg, rgba(226, 190, 118, 0.3), rgba(226, 190, 118, 0.12));
  border: 1px solid rgba(226, 190, 118, 0.55);
  box-shadow: 0 0 8px rgba(226, 190, 118, 0.25);
  white-space: nowrap;
}

.seat-name {
  font-size: $font-size-sm;
  color: $text-primary;
  display: flex;
  align-items: center;
  gap: 4px;
  max-width: 84px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;

  // 真人玩家恒用黄色（自己始终知道自己的身份），与发言流/投票行的高亮一致
  &.is-me {
    color: #ffff00;
  }
}

.me-tag {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--c-primary);
  border: 1px solid var(--c-primary);
  border-radius: 4px;
  padding: 0 3px;
  line-height: 14px;
}

.seat-tags {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 18px;
}

.seat-role {
  font-size: $font-size-xs;
  border-radius: 4px;
  padding: 0 6px;
  line-height: 16px;

  &.wolf {
    color: #eba3a0;
    background: $wolf-color-light;
  }

  &.god {
    color: #aac8ec;
    background: $god-color-light;
  }

  &.villager {
    color: #a8c8a8;
    background: rgba(143, 191, 143, 0.14);
  }
}

.vote-lost-tag {
  display: inline-block;
  margin-left: 4px;
  font-size: 10px;
  line-height: 14px;
  padding: 0 4px;
  border-radius: 4px;
  color: $text-secondary;
  background: rgba(255, 255, 255, 0.08);
}

.selected-ring {
  position: absolute;
  inset: -3px;
  border: 2px dashed $wolf-color;
  border-radius: $radius-lg;
  pointer-events: none;
}
</style>
