<script setup lang="ts">
defineProps<{ updatedAt?: string | null; label?: string }>();
</script>

<template>
  <p class="freshness">
    <i class="pi pi-clock" aria-hidden="true" />
    {{ label ?? "最近更新" }}：{{
      updatedAt ? new Date(updatedAt).toLocaleString("zh-CN") : "等待真实数据"
    }}
  </p>
</template>

<style scoped>
.freshness {
  margin: 0;
  color: var(--lk-text-muted);
  font-size: 0.9rem;
}
</style>
