<script setup lang="ts">
withDefaults(defineProps<{ as?: "section" | "article" | "div" }>(), {
  as: "section",
});
</script>

<template>
  <component :is="as" class="app-surface">
    <slot />
  </component>
</template>

<style scoped>
.app-surface {
  padding: clamp(18px, 3vw, 28px);
  border: 1px solid var(--lk-border);
  border-radius: var(--lk-radius-surface);
  background: var(--lk-surface);
  box-shadow: var(--lk-shadow);
}
</style>
