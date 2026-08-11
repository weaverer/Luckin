<script setup lang="ts">
import { ref } from "vue";

import { useWatchlists } from "@/composables/useWatchlists";

const props = defineProps<{ stockId: string }>();
const selectedGroup = ref("");
const message = ref("");
const watchlists = useWatchlists();

async function addToWatchlist(): Promise<void> {
  if (!selectedGroup.value || !props.stockId) return;
  try {
    await watchlists.add(selectedGroup.value, props.stockId);
    message.value = "已加入自选组合，不会产生任何交易行为。";
  } catch {
    message.value = "加入自选失败，可能已经存在于该分组。";
  }
}
</script>

<template>
  <div class="watchlist-action">
    <select v-model="selectedGroup" aria-label="选择自选分组">
      <option value="">选择分组</option>
      <option
        v-for="group in watchlists.groups.value"
        :key="group.group_id"
        :value="group.group_id"
      >
        {{ group.name }}
      </option>
    </select>
    <button
      :disabled="!selectedGroup || watchlists.saving.value"
      @click="addToWatchlist"
    >
      加入自选
    </button>
  </div>
  <p v-if="message" role="status">{{ message }}</p>
</template>

<style scoped>
.watchlist-action {
  display: flex;
  gap: 8px;
}
select,
button {
  min-height: 44px;
  padding: 0 10px;
  border: 1px solid var(--lk-border);
  border-radius: 8px;
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}
p {
  margin: 10px 0 0;
  color: var(--lk-text-secondary);
  font-size: 0.8rem;
}
</style>
