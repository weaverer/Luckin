<script setup lang="ts">
import { computed, reactive } from "vue";
import DatePicker from "primevue/datepicker";
import Dialog from "primevue/dialog";

import type { ImportantDate } from "@/composables/useCalendar";
import { formatIsoDate, parseIsoDate } from "@/utils/date";

const props = defineProps<{
  initialDate: string;
  importantDate?: ImportantDate | null;
  fieldErrors?: Record<string, string>;
  error?: string;
  busy?: boolean;
}>();
const emit = defineEmits<{
  save: [value: { event_date: string; title: string; notes: string | null }];
  cancel: [];
}>();
const form = reactive({
  event_date:
    props.importantDate?.event_date || props.initialDate
      ? parseIsoDate(props.importantDate?.event_date ?? props.initialDate)
      : null,
  title: props.importantDate?.title ?? "",
  notes: props.importantDate?.notes ?? "",
});
const localErrors = reactive<Record<string, string>>({});
const heading = computed(() =>
  props.importantDate ? "编辑重要日" : "添加重要日",
);

function submit(): void {
  const eventDate = form.event_date;
  localErrors.event_date = eventDate ? "" : "请选择日期";
  localErrors.title = form.title.trim() ? "" : "请输入标题";
  if (!eventDate || localErrors.event_date || localErrors.title) return;
  emit("save", {
    event_date: formatIsoDate(eventDate),
    title: form.title.trim(),
    notes: form.notes || null,
  });
}
</script>

<template>
  <Dialog
    :visible="true"
    modal
    append-to="self"
    :header="heading"
    class="important-date-dialog"
    :style="{ width: 'min(32rem, 92vw)' }"
    @update:visible="emit('cancel')"
  >
    <form @submit.prevent="submit">
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <label for="important-date-date">
        日期
        <DatePicker
          v-model="form.event_date"
          input-id="important-date-date"
          date-format="yy-mm-dd"
          show-icon
          :show-on-focus="false"
          fluid
          required
        />
        <small
          v-if="localErrors.event_date || fieldErrors?.event_date"
          class="field-error"
        >
          {{ localErrors.event_date || fieldErrors?.event_date }}
        </small>
      </label>
      <label>
        标题
        <input v-model="form.title" maxlength="120" required />
        <small
          v-if="localErrors.title || fieldErrors?.title"
          class="field-error"
        >
          {{ localErrors.title || fieldErrors?.title }}
        </small>
      </label>
      <label>备注<textarea v-model="form.notes" maxlength="1000" /></label>
      <div>
        <button type="button" @click="emit('cancel')">取消</button>
        <button type="submit" :disabled="busy">
          {{ busy ? "保存中…" : "保存" }}
        </button>
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
form {
  display: grid;
  gap: 14px;
}
label {
  display: grid;
  gap: 6px;
}
textarea,
button {
  padding: 10px;
  border: 1px solid var(--lk-border);
  border-radius: 9px;
}
form div {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.form-error,
.field-error {
  margin: 0;
  color: var(--lk-danger);
}
</style>
