<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [v: boolean]
  confirm: [board: { name: string; roles: string[]; win_condition: string; max_days: number }]
}>()

const ROLE_DEFS = [
  { role: 'werewolf', label: '狼人', min: 1, max: 4 },
  { role: 'villager', label: '村民', min: 0, max: 8 },
  { role: 'seer', label: '预言家', min: 0, max: 1 },
  { role: 'witch', label: '女巫', min: 0, max: 1 },
  { role: 'hunter', label: '猎人', min: 0, max: 1 },
  { role: 'idiot', label: '白痴', min: 0, max: 1 },
  { role: 'guard', label: '守卫', min: 0, max: 1 },
]

const counts = ref<Record<string, number>>({ werewolf: 2, villager: 2, seer: 1, witch: 1, hunter: 0, idiot: 0, guard: 0 })
const winCondition = ref<'city' | 'side'>('city')
const maxDays = ref(20)
const name = ref('')

const total = computed(() => Object.values(counts.value).reduce((a, b) => a + b, 0))

const error = computed(() => {
  if (total.value < 5 || total.value > 12) return '人数需在 5~12 之间'
  if (counts.value.werewolf < 1) return '至少 1 名狼人'
  if (counts.value.werewolf >= total.value) return '狼人不能占满全场'
  if (winCondition.value === 'side') {
    const gods = counts.value.seer + counts.value.witch + counts.value.hunter + counts.value.idiot + counts.value.guard
    if (gods < 1 || counts.value.villager < 1) return '屠边局神职侧与村民侧都至少 1 名（白痴算神职）'
  }
  return ''
})

const rolesPayload = computed(() => {
  const roles: string[] = []
  for (const def of ROLE_DEFS) {
    for (let i = 0; i < (counts.value[def.role] ?? 0); i++) roles.push(def.role)
  }
  return roles
})

watch(
  () => props.modelValue,
  (v) => {
    if (v) {
      counts.value = { werewolf: 2, villager: 2, seer: 1, witch: 1, hunter: 0, idiot: 0, guard: 0 }
      winCondition.value = 'city'
      maxDays.value = 20
      name.value = ''
    }
  },
)

function submit() {
  if (error.value) return
  emit('update:modelValue', false)
  emit('confirm', {
    name: name.value.trim() || `自定义${total.value}人局`,
    roles: rolesPayload.value,
    win_condition: winCondition.value,
    max_days: maxDays.value,
  })
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="modal-mask" @click.self="emit('update:modelValue', false)">
      <div class="modal">
        <div class="modal-title">自定义板子</div>
        <div class="role-list">
          <div v-for="def in ROLE_DEFS" :key="def.role" class="role-row">
            <span class="role-name">{{ def.label }}</span>
            <div class="stepper">
              <button class="step-btn" :disabled="counts[def.role] <= def.min" @click="counts[def.role]--">−</button>
              <span class="step-num">{{ counts[def.role] }}</span>
              <button class="step-btn" :disabled="counts[def.role] >= def.max" @click="counts[def.role]++">＋</button>
            </div>
          </div>
        </div>
        <div class="form-row">
          <span class="form-label">胜利条件</span>
          <select v-model="winCondition" class="input cond-select">
            <option value="city">屠城（狼数 ≥ 好人数）</option>
            <option value="side">屠边（神职或村民全灭）</option>
          </select>
        </div>
        <div class="form-row">
          <span class="form-label">天数上限</span>
          <input v-model.number="maxDays" type="number" min="1" max="99" class="input days-input" />
        </div>
        <div class="form-row">
          <span class="form-label">板子名称</span>
          <input v-model="name" class="input" placeholder="留空自动命名" />
        </div>
        <div class="modal-foot">
          <span class="total-line">共 {{ total }} 人 <span v-if="error" class="err">（{{ error }}）</span></span>
          <button class="btn" @click="emit('update:modelValue', false)">取消</button>
          <button class="btn primary" :disabled="!!error" @click="submit">使用该板子</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 16, 0.68);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 300;
}

.modal {
  width: 420px;
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-xl;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(var(--c-primary-rgb), 0.08);
}

.modal-title {
  font-size: $font-size-lg;
  font-weight: 600;
}

.role-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.role-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.role-name {
  font-size: $font-size-base;
}

.stepper {
  display: flex;
  align-items: center;
  gap: 10px;
}

.step-btn {
  width: 26px;
  height: 26px;
  border-radius: $radius-sm;
  background: $bg-input;
  cursor: pointer;
  font-size: $font-size-md;

  &:hover:not(:disabled) {
    background: $bg-hover;
  }

  &:disabled {
    opacity: 0.35;
    cursor: not-allowed;
  }
}

.step-num {
  min-width: 20px;
  text-align: center;
  font-weight: 600;
}

.form-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.form-label {
  width: 64px;
  color: $text-secondary;
  font-size: $font-size-sm;
  flex-shrink: 0;
}

.cond-select {
  flex: 1;
}

.days-input {
  width: 90px;
}

.modal-foot {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.total-line {
  margin-right: auto;
  color: $text-secondary;
  font-size: $font-size-sm;
}

.err {
  color: #d09a9a;
}
</style>
