<script setup lang="ts">
import AppSurface from "./AppSurface.vue";

defineProps<{ title: string; description: string }>();
</script>

<template>
  <AppSurface class="pending-state">
    <span class="status"
      ><i class="pi pi-clock" aria-hidden="true" /> 尚未交付</span
    >
    <h2>{{ title }}</h2>
    <p>{{ description }}</p>
    <p class="notice">
      此处不会使用静态业务假数据。功能交付后将连接真实 API 状态。
    </p>
  </AppSurface>
</template>

<style scoped>
.pending-state {
  display: grid;
  gap: 10px;
}

.status {
  width: fit-content;
  padding: 6px 10px;
  border-radius: 999px;
  color: var(--lk-warning);
  background: color-mix(in srgb, var(--lk-warning) 12%, transparent);
  font-size: 0.85rem;
}

h2,
p {
  margin: 0;
}

.notice {
  color: var(--lk-text-secondary);
}
</style>
