<script setup lang="ts">
import { useThemeStore } from "@/stores/theme";

const theme = useThemeStore();
defineProps<{ menuCollapsed: boolean }>();
defineEmits<{ toggleMenu: [] }>();
</script>

<template>
  <header class="topbar">
    <div class="topbar-identity">
      <button
        type="button"
        :aria-label="menuCollapsed ? '展开主菜单' : '折叠主菜单'"
        :aria-expanded="!menuCollapsed"
        @click="$emit('toggleMenu')"
      >
        <i class="pi pi-bars" aria-hidden="true" />
      </button>
      <p>数据投资工作台</p>
    </div>
    <div class="topbar-actions">
      <span class="market-session"><i aria-hidden="true" /> CN-S 工作台</span>
      <button
        type="button"
        :aria-label="theme.isDark ? '切换亮色主题' : '切换暗色主题'"
        @click="theme.toggle"
      >
        <i
          class="pi"
          :class="theme.isDark ? 'pi-sun' : 'pi-moon'"
          aria-hidden="true"
        />
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 64px;
  color: var(--lk-text-secondary);
}
.topbar-identity,
.topbar-actions,
.market-session {
  display: flex;
  align-items: center;
  gap: 10px;
}
.topbar p {
  margin: 0;
  font-weight: 700;
}
.market-session {
  color: var(--lk-text-muted);
  font-size: 0.78rem;
}
.market-session i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--lk-success);
}

button {
  display: grid;
  width: 44px;
  height: 44px;
  place-items: center;
  border: 1px solid var(--lk-border);
  border-radius: 50%;
  color: var(--lk-text);
  background: var(--lk-surface);
  cursor: pointer;
}
@media (max-width: 620px) {
  .market-session {
    display: none;
  }
}
</style>
