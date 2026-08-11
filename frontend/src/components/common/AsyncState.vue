<script setup lang="ts">
import AppSurface from "./AppSurface.vue";

type State = "loading" | "empty" | "error" | "stale" | "ready";

withDefaults(
  defineProps<{
    state: State;
    title?: string;
    message?: string;
    refreshable?: boolean;
  }>(),
  { title: "", message: "", refreshable: false },
);
defineEmits<{ refresh: [] }>();

const labels: Record<
  Exclude<State, "ready">,
  { icon: string; fallback: string }
> = {
  loading: { icon: "pi-spin pi-spinner", fallback: "正在加载" },
  empty: { icon: "pi-inbox", fallback: "暂无数据" },
  error: { icon: "pi-exclamation-circle", fallback: "加载失败" },
  stale: { icon: "pi-history", fallback: "数据可能已过期" },
};
</script>

<template>
  <slot v-if="state === 'ready'" />
  <AppSurface
    v-else
    class="async-state"
    role="status"
    :aria-live="state === 'loading' ? 'polite' : 'assertive'"
  >
    <i class="pi" :class="labels[state].icon" aria-hidden="true" />
    <h2>{{ title || labels[state].fallback }}</h2>
    <p v-if="message">{{ message }}</p>
    <button v-if="refreshable" type="button" @click="$emit('refresh')">
      重新加载
    </button>
  </AppSurface>
</template>

<style scoped>
.async-state {
  display: grid;
  justify-items: start;
  gap: 10px;
  color: var(--lk-text-secondary);
}

.async-state > .pi {
  font-size: 1.5rem;
}

h2,
p {
  margin: 0;
}

button {
  min-height: 40px;
  padding: 0 14px;
  border: 0;
  border-radius: 10px;
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
  cursor: pointer;
}
</style>
