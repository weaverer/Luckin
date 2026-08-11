<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import AutoComplete, {
  type AutoCompleteCompleteEvent,
} from "primevue/autocomplete";
import Dialog from "primevue/dialog";
import Tab from "primevue/tab";
import TabList from "primevue/tablist";
import TabPanel from "primevue/tabpanel";
import TabPanels from "primevue/tabpanels";
import Tabs from "primevue/tabs";

import AppSurface from "@/components/common/AppSurface.vue";
import AsyncState from "@/components/common/AsyncState.vue";
import { useStockPicker } from "@/composables/useStocks";
import {
  type WatchlistGroup,
  type WatchlistGroupInput,
  useWatchlists,
} from "@/composables/useWatchlists";
import { venueLabels } from "@/utils/market";

const watchlists = useWatchlists();
const stocks = useStockPicker();
const activeGroupId = ref("");
const selectedStocks = ref<Record<string, string>>({});
const actionError = ref("");
const dialogVisible = ref(false);
const editingGroupId = ref<string | null>(null);
const tagSuggestions = ref<string[]>([]);
const newTag = ref("");
const draggingGroupId = ref("");
const form = reactive<WatchlistGroupInput>({ name: "", notes: "", tags: [] });
const formErrors = reactive({ name: "", notes: "", tags: "" });
const state = computed(() =>
  watchlists.loading.value
    ? "loading"
    : watchlists.error.value
      ? "error"
      : watchlists.groups.value.length
        ? "ready"
        : "empty",
);
const allTags = computed(() => [
  ...new Set(watchlists.groups.value.flatMap((group) => group.tags)),
]);

watch(
  () => watchlists.groups.value,
  (groups) => {
    if (!groups.some((group) => group.group_id === activeGroupId.value)) {
      activeGroupId.value = groups[0]?.group_id ?? "";
    }
  },
  { immediate: true },
);

async function perform(action: () => Promise<unknown>): Promise<boolean> {
  actionError.value = "";
  try {
    await action();
    return true;
  } catch {
    actionError.value = "操作未完成，请检查输入后重试";
    return false;
  }
}

function resetForm(group?: WatchlistGroup): void {
  form.name = group?.name ?? "";
  form.notes = group?.notes ?? "";
  form.tags = [...(group?.tags ?? [])];
  formErrors.name = "";
  formErrors.notes = "";
  formErrors.tags = "";
  newTag.value = "";
}

function openCreate(): void {
  editingGroupId.value = null;
  resetForm();
  dialogVisible.value = true;
}

function openEdit(group: WatchlistGroup): void {
  editingGroupId.value = group.group_id;
  resetForm(group);
  dialogVisible.value = true;
}

function addTag(): void {
  const value = newTag.value.trim();
  if (value && !form.tags.includes(value)) form.tags.push(value);
  newTag.value = "";
}

function completeTags(event: AutoCompleteCompleteEvent): void {
  const query = event.query.trim().toLocaleLowerCase("zh-CN");
  tagSuggestions.value = allTags.value.filter(
    (tag) =>
      !form.tags.includes(tag) &&
      tag.toLocaleLowerCase("zh-CN").includes(query),
  );
}

async function saveGroup(): Promise<void> {
  formErrors.name = form.name.trim() ? "" : "请输入分组名称";
  formErrors.notes = form.notes.trim() ? "" : "请输入分组备注";
  formErrors.tags = form.tags.length ? "" : "请至少添加一个标签";
  if (formErrors.name || formErrors.notes || formErrors.tags) return;
  const input = {
    name: form.name.trim(),
    notes: form.notes.trim(),
    tags: [...form.tags],
  };
  const saved = editingGroupId.value
    ? await perform(() => watchlists.update(editingGroupId.value!, input))
    : await perform(() => watchlists.create(input));
  if (saved) dialogVisible.value = false;
}

async function deleteGroup(groupId: string, groupName: string): Promise<void> {
  if (confirm("确认删除“" + groupName + "”及其中全部自选股票？")) {
    await perform(() => watchlists.deleteGroup(groupId));
  }
}

async function add(groupId: string): Promise<void> {
  const stockId = selectedStocks.value[groupId];
  if (!stockId) return;
  const saved = await perform(() => watchlists.add(groupId, stockId));
  if (saved) selectedStocks.value[groupId] = "";
}

async function remove(
  groupId: string,
  stockId: string,
  stockName: string,
): Promise<void> {
  if (confirm("确认将“" + stockName + "”移出该分组？")) {
    await perform(() => watchlists.remove(groupId, stockId));
  }
}

function startDrag(groupId: string): void {
  draggingGroupId.value = groupId;
}

async function dropOn(targetGroupId: string): Promise<void> {
  const sourceGroupId = draggingGroupId.value;
  draggingGroupId.value = "";
  if (!sourceGroupId || sourceGroupId === targetGroupId) return;
  const ordered = watchlists.groups.value.map((group) => group.group_id);
  ordered.splice(ordered.indexOf(sourceGroupId), 1);
  ordered.splice(ordered.indexOf(targetGroupId), 0, sourceGroupId);
  activeGroupId.value = sourceGroupId;
  await perform(() => watchlists.reorder(ordered));
}

function canMoveGroup(groupId: string, offset: -1 | 1): boolean {
  const index = watchlists.groups.value.findIndex(
    (group) => group.group_id === groupId,
  );
  const targetIndex = index + offset;
  return (
    index >= 0 &&
    targetIndex >= 0 &&
    targetIndex < watchlists.groups.value.length
  );
}

async function moveGroup(groupId: string, offset: -1 | 1): Promise<void> {
  if (!canMoveGroup(groupId, offset)) return;
  const ordered = watchlists.groups.value.map((group) => group.group_id);
  const index = ordered.indexOf(groupId);
  const targetId = ordered[index + offset]!;
  ordered[index] = targetId;
  ordered[index + offset] = groupId;
  activeGroupId.value = groupId;
  await perform(() => watchlists.reorder(ordered));
}
</script>

<template>
  <div class="page-stack">
    <header class="page-header">
      <div>
        <p class="eyebrow">个人股票清单</p>
        <h1 class="page-heading">自选分组</h1>
      </div>
      <button
        class="primary"
        type="button"
        aria-label="添加分组"
        @click="openCreate"
      >
        <i class="pi pi-plus" aria-hidden="true" /> 添加分组
      </button>
    </header>

    <p v-if="actionError" class="inline-error" role="alert">
      <i class="pi pi-exclamation-circle" aria-hidden="true" />
      {{ actionError }}
    </p>

    <AsyncState
      :state="state"
      :title="state === 'empty' ? '还没有自选分组' : ''"
      :message="
        state === 'empty'
          ? '点击“添加分组”，建立你的第一组关注标的。'
          : watchlists.error.value
      "
      refreshable
      @refresh="watchlists.refresh"
    >
      <Tabs v-model:value="activeGroupId" class="watchlist-tabs">
        <TabList
          class="group-tab-list"
          aria-label="自选分组"
          aria-orientation="vertical"
        >
          <Tab
            v-for="group in watchlists.groups.value"
            :key="group.group_id"
            :value="group.group_id"
            draggable="true"
            :class="{ dragging: draggingGroupId === group.group_id }"
            @dragstart="startDrag(group.group_id)"
            @dragend="draggingGroupId = ''"
            @dragover.prevent
            @drop.prevent="dropOn(group.group_id)"
            @keydown.alt.up.prevent="moveGroup(group.group_id, -1)"
            @keydown.alt.down.prevent="moveGroup(group.group_id, 1)"
          >
            <span class="drag-handle" aria-hidden="true">
              <i class="pi pi-bars" />
            </span>
            <span class="tab-copy">
              <b>{{ group.name }}</b>
              <small>{{ group.members.length }} 只股票</small>
            </span>
          </Tab>
        </TabList>

        <TabPanels>
          <TabPanel
            v-for="group in watchlists.groups.value"
            :key="group.group_id"
            :value="group.group_id"
          >
            <AppSurface as="section" class="group-panel">
              <header class="group-header">
                <div>
                  <h2>{{ group.name }}</h2>
                  <p>{{ group.notes }}</p>
                  <div class="group-tags" aria-label="分组标签">
                    <span v-for="tag in group.tags" :key="tag">{{ tag }}</span>
                  </div>
                </div>
                <div class="group-actions">
                  <div class="order-actions" aria-label="调整分组顺序">
                    <button
                      type="button"
                      :aria-label="`上移${group.name}`"
                      title="上移分组（Alt + ↑）"
                      :disabled="!canMoveGroup(group.group_id, -1)"
                      @click="moveGroup(group.group_id, -1)"
                    >
                      <i class="pi pi-arrow-up" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      :aria-label="`下移${group.name}`"
                      title="下移分组（Alt + ↓）"
                      :disabled="!canMoveGroup(group.group_id, 1)"
                      @click="moveGroup(group.group_id, 1)"
                    >
                      <i class="pi pi-arrow-down" aria-hidden="true" />
                    </button>
                  </div>
                  <button type="button" @click="openEdit(group)">
                    <i class="pi pi-pencil" aria-hidden="true" /> 编辑
                  </button>
                  <button
                    class="danger"
                    type="button"
                    @click="deleteGroup(group.group_id, group.name)"
                  >
                    <i class="pi pi-trash" aria-hidden="true" /> 删除
                  </button>
                </div>
              </header>

              <form
                class="member-toolbar"
                role="search"
                aria-label="查找并添加股票"
                @submit.prevent="add(group.group_id)"
              >
                <label for="watchlist-stock-search">
                  查找股票
                  <input
                    id="watchlist-stock-search"
                    v-model.trim="stocks.draftQuery.value"
                    placeholder="输入代码或名称"
                  />
                </label>
                <button type="button" @click="stocks.search()">查找</button>
                <label class="stock-select" :for="'stock-' + group.group_id">
                  选择股票
                  <select
                    :id="'stock-' + group.group_id"
                    v-model="selectedStocks[group.group_id]"
                    required
                  >
                    <option value="">选择搜索结果</option>
                    <option
                      v-for="stock in stocks.items.value"
                      :key="stock.stock_id"
                      :value="stock.stock_id"
                    >
                      {{ stock.security_code }} · {{ stock.name }}
                    </option>
                  </select>
                </label>
                <button
                  class="primary"
                  type="submit"
                  :disabled="watchlists.saving.value"
                >
                  加入分组
                </button>
                <span
                  v-if="stocks.fetching.value"
                  class="toolbar-status"
                  role="status"
                >
                  更新中…
                </span>
                <span
                  v-else-if="stocks.error.value"
                  class="toolbar-status inline-error"
                  role="alert"
                >
                  {{ stocks.error.value }}
                </span>
                <span v-else class="toolbar-status muted">
                  当前共 {{ stocks.items.value.length }} 个候选
                </span>
              </form>

              <p v-if="!group.members.length" class="empty-members">
                这个分组还没有股票。先查找股票，再加入当前分组。
              </p>
              <div v-else class="members">
                <div
                  v-for="member in group.members"
                  :key="member.member_id"
                  class="member"
                >
                  <RouterLink
                    :to="{
                      name: 'stock-detail',
                      params: { stockId: member.stock.stock_id },
                    }"
                  >
                    <strong class="numeric">{{
                      member.stock.security_code
                    }}</strong>
                    <span>{{ member.stock.name }}</span>
                  </RouterLink>
                  <span class="venue">
                    {{
                      venueLabels[member.stock.venue_code] ??
                      member.stock.venue_code
                    }}
                  </span>
                  <button
                    type="button"
                    aria-label="移出股票"
                    @click="
                      remove(
                        group.group_id,
                        member.stock.stock_id,
                        member.stock.name,
                      )
                    "
                  >
                    移出
                  </button>
                </div>
              </div>
            </AppSurface>
          </TabPanel>
        </TabPanels>
      </Tabs>
    </AsyncState>

    <Dialog
      v-model:visible="dialogVisible"
      modal
      append-to="self"
      :header="editingGroupId ? '编辑分组' : '添加分组'"
      :style="{ width: 'min(34rem, 92vw)' }"
    >
      <form class="group-form" @submit.prevent="saveGroup">
        <label for="group-name">
          分组名称 <em>必填</em>
          <input
            id="group-name"
            v-model="form.name"
            maxlength="80"
            placeholder="例如 长线观察"
          />
          <small v-if="formErrors.name" class="field-error">{{
            formErrors.name
          }}</small>
        </label>
        <label for="group-notes">
          备注 <em>必填</em>
          <textarea
            id="group-notes"
            v-model="form.notes"
            maxlength="1000"
            rows="4"
            placeholder="记录分组策略、观察条件或调整原则"
          />
          <small v-if="formErrors.notes" class="field-error">{{
            formErrors.notes
          }}</small>
        </label>
        <label for="group-tags">
          标签 <em>必填</em>
          <AutoComplete
            v-model="form.tags"
            input-id="group-tags"
            multiple
            fluid
            :suggestions="tagSuggestions"
            placeholder="输入以搜索已有标签"
            @complete="completeTags"
          />
          <small>已有标签可重复用于不同分组，也可以在下方新增。</small>
          <small v-if="formErrors.tags" class="field-error">{{
            formErrors.tags
          }}</small>
        </label>
        <div class="new-tag-row">
          <input
            v-model="newTag"
            maxlength="30"
            aria-label="新标签"
            placeholder="输入新标签"
            @keydown.enter.prevent="addTag"
          />
          <button type="button" @click="addTag">添加标签</button>
        </div>
        <div class="dialog-actions">
          <button type="button" @click="dialogVisible = false">取消</button>
          <button
            class="primary"
            type="submit"
            :disabled="watchlists.saving.value"
          >
            {{ watchlists.saving.value ? "保存中…" : "保存分组" }}
          </button>
        </div>
      </form>
    </Dialog>
  </div>
</template>

<style scoped>
.page-header,
.group-header,
.group-actions,
.order-actions,
.member,
.new-tag-row,
.dialog-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.order-actions {
  justify-content: flex-start;
  gap: 6px;
  padding-right: 10px;
  border-right: 1px solid var(--lk-border);
}
.order-actions button {
  width: 42px;
  padding: 0;
}
.eyebrow {
  margin: 0 0 6px;
  color: var(--lk-text-muted);
  font-size: 0.72rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  text-transform: uppercase;
}
button,
input,
select,
textarea {
  min-height: 42px;
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text);
  background: var(--lk-surface);
}
textarea {
  padding-block: 10px;
  resize: vertical;
}
button {
  cursor: pointer;
}
button.primary {
  border-color: var(--lk-primary);
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}
button.danger,
.inline-error,
.field-error {
  color: var(--lk-danger);
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.inline-error {
  margin: 0;
}
:deep(.watchlist-tabs) {
  display: grid;
  grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  align-items: start;
  gap: 16px;
}
:deep(.watchlist-tabs > .p-tablist) {
  position: sticky;
  top: 76px;
  overflow: hidden;
  border-right: 1px solid var(--lk-border);
  background: transparent;
}
:deep(.watchlist-tabs .p-tablist-tab-list) {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 14px 0 0;
  border: 0;
  background: transparent;
}
:deep(.watchlist-tabs .p-tab) {
  width: 100%;
  justify-content: flex-start;
  gap: 10px;
  padding: 12px 14px;
  border: 0;
  border-radius: 10px;
  transition:
    background 140ms ease,
    opacity 140ms ease;
}
:deep(.watchlist-tabs .p-tab-active) {
  color: var(--lk-text);
  background: var(--lk-surface);
  box-shadow:
    inset 3px 0 var(--lk-primary),
    0 1px 4px color-mix(in srgb, var(--lk-text) 8%, transparent);
}
:deep(.watchlist-tabs .p-tablist-active-bar) {
  display: none;
}
:deep(.watchlist-tabs .p-tabpanels) {
  min-width: 0;
  padding: 0;
  background: transparent;
}
:deep(.watchlist-tabs .p-tab.dragging) {
  opacity: 0.45;
}
.drag-handle {
  color: var(--lk-text-muted);
  cursor: grab;
}
.tab-copy {
  display: grid;
  gap: 3px;
  text-align: left;
}
.tab-copy small {
  color: var(--lk-text-muted);
  font-size: 0.7rem;
}
.group-panel {
  box-shadow: none;
}
.group-header {
  align-items: flex-start;
}
.group-header h2,
.group-header p {
  margin: 0;
}
.group-header p {
  margin-top: 6px;
  color: var(--lk-text-secondary);
}
.group-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}
.group-tags span {
  padding: 4px 8px;
  border-radius: 999px;
  color: var(--lk-primary);
  background: var(--lk-selection);
  font-size: 0.72rem;
  font-weight: 700;
}
.member-toolbar {
  display: grid;
  grid-template-columns: minmax(170px, 0.8fr) auto minmax(240px, 1.2fr) auto;
  align-items: end;
  gap: 10px;
  margin-top: 18px;
  padding: 12px;
  border: 1px solid var(--lk-border);
  border-radius: 12px;
  background: var(--lk-surface-soft);
}
.member-toolbar > button {
  min-width: 84px;
}
.toolbar-status {
  grid-column: 1 / -1;
  min-height: 18px;
  font-size: 0.75rem;
}
label {
  display: grid;
  flex: 1;
  gap: 6px;
  color: var(--lk-text-secondary);
  font-size: 0.82rem;
}
label em {
  color: var(--lk-danger);
  font-style: normal;
}
label small {
  color: var(--lk-text-muted);
}
.empty-members {
  margin: 20px 0 0;
  color: var(--lk-text-muted);
}
.member {
  min-height: 56px;
  border-top: 1px solid var(--lk-border);
}
.member a {
  display: grid;
  flex: 1;
  gap: 3px;
  color: var(--lk-text);
  text-decoration: none;
}
.member a span,
.venue {
  color: var(--lk-text-secondary);
  font-size: 0.8rem;
}
.group-form {
  display: grid;
  gap: 16px;
}
.new-tag-row input {
  flex: 1;
}
.dialog-actions {
  justify-content: flex-end;
  padding-top: 4px;
}
.numeric {
  font-variant-numeric: tabular-nums;
}
@media (max-width: 760px) {
  :deep(.watchlist-tabs) {
    grid-template-columns: 1fr;
  }
  :deep(.watchlist-tabs > .p-tablist) {
    position: static;
    border-right: 0;
    border-bottom: 1px solid var(--lk-border);
  }
  :deep(.watchlist-tabs .p-tablist-tab-list) {
    flex-direction: row;
    overflow-x: auto;
  }
  :deep(.watchlist-tabs .p-tab) {
    width: auto;
    min-width: 160px;
  }
  .member-toolbar {
    grid-template-columns: minmax(0, 1fr) auto;
  }
}
@media (max-width: 620px) {
  .group-header {
    align-items: stretch;
    flex-direction: column;
  }
  .member-toolbar {
    grid-template-columns: 1fr;
  }
  .toolbar-status {
    grid-column: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  :deep(.watchlist-tabs .p-tab) {
    transition: none;
  }
}
</style>
