<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ToastHost from '@/components/common/ToastHost.vue'

// 桌面壳的关闭确认：主进程拦截点 X 后发 close-request，这里弹窗让用户选最小化/退出，
// 勾选「不再询问」后偏好持久化在主进程（托盘右键菜单可改回来）
const closeOpen = ref(false)
const closeRemember = ref(false)

onMounted(() => {
  const w = (window as any).werewolf
  if (!w?.onCloseRequest) return
  w.closeUiReady()
  w.onCloseRequest(() => {
    closeRemember.value = false
    closeOpen.value = true
  })
})

function chooseClose(choice: 'minimize' | 'exit') {
  closeOpen.value = false
  ;(window as any).werewolf?.closeChoice(choice, closeRemember.value)
}
</script>

<template>
  <router-view />
  <ToastHost />
  <Teleport to="body">
    <div v-if="closeOpen" class="close-mask" @click.self="closeOpen = false">
      <div class="close-box" role="alertdialog" aria-modal="true">
        <div class="close-head">
          <span class="close-gem" />
          <div class="close-title">关闭窗口</div>
        </div>
        <div class="close-message">
          最小化到托盘后游戏与对局继续运行，可随时从托盘图标回到窗口；退出则会结束应用，进行中的对局将中断。
        </div>
        <label class="close-remember">
          <input v-model="closeRemember" type="checkbox" />
          不再询问，记住我的选择（可在托盘右键菜单中修改）
        </label>
        <div class="close-foot">
          <button class="btn" @click="closeOpen = false">取消</button>
          <button class="btn" @click="chooseClose('minimize')">最小化到托盘</button>
          <button class="btn primary" @click="chooseClose('exit')">退出应用</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped lang="scss">
.close-mask {
  position: fixed;
  inset: 0;
  background: rgba(8, 6, 16, 0.68);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 320;
}

.close-box {
  width: 400px;
  background: $bg-panel;
  border: 1px solid $border-color;
  border-radius: $radius-lg;
  padding: $spacing-lg $spacing-xl;
  display: flex;
  flex-direction: column;
  gap: $spacing-md;
  box-shadow: 0 12px 48px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(var(--c-primary-rgb), 0.08);
}

.close-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.close-gem {
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: linear-gradient(180deg, $night-accent, var(--c-primary));
  box-shadow: 0 0 10px rgba(var(--c-primary-rgb), 0.45);
  flex-shrink: 0;
}

.close-title {
  font-size: $font-size-md;
  font-weight: 600;
  color: $text-primary;
}

.close-message {
  font-size: $font-size-sm;
  color: $text-secondary;
  line-height: 1.7;
  padding-left: 14px;
}

.close-remember {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: $font-size-xs;
  color: $text-secondary;
  cursor: pointer;
  padding-left: 14px;

  input {
    accent-color: var(--c-primary);
  }
}

.close-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-left: 14px;

  .btn {
    min-width: 76px;
  }
}
</style>
