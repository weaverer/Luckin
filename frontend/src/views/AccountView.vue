<script setup lang="ts">
import { reactive } from "vue";
import { useRouter } from "vue-router";

import AppSurface from "@/components/common/AppSurface.vue";
import { useAuth } from "@/composables/useAuth";

const auth = useAuth();
const router = useRouter();
const form = reactive({ currentPassword: "", newPassword: "" });

async function changePassword(): Promise<void> {
  try {
    await auth.changePassword(form.currentPassword, form.newPassword);
    await router.replace({ name: "login" });
  } catch {
    return;
  }
}

async function logout(): Promise<void> {
  await auth.logout();
  await router.replace({ name: "login" });
}
</script>

<template>
  <div class="page-stack">
    <h1 class="page-heading">账号设置</h1>
    <AppSurface>
      <h2>{{ auth.session.user?.displayName ?? "当前用户" }}</h2>
      <p class="muted">{{ auth.session.user?.username }}</p>
      <button type="button" class="secondary" @click="logout">安全退出</button>
    </AppSurface>
    <AppSurface>
      <h2>修改密码</h2>
      <form @submit.prevent="changePassword">
        <label
          >当前密码<input
            v-model="form.currentPassword"
            type="password"
            autocomplete="current-password"
            required
        /></label>
        <label
          >新密码<input
            v-model="form.newPassword"
            type="password"
            autocomplete="new-password"
            minlength="12"
            maxlength="128"
            required
        /></label>
        <p v-if="auth.errorMessage.value" role="alert">
          {{ auth.errorMessage.value }}
        </p>
        <button type="submit" :disabled="auth.loading.value">
          保存并重新登录
        </button>
      </form>
    </AppSurface>
  </div>
</template>

<style scoped>
h2,
p {
  margin-top: 0;
}

form,
label {
  display: grid;
  gap: 8px;
}

form {
  max-width: 480px;
  gap: 16px;
}

input,
button {
  min-height: 42px;
  border-radius: 10px;
}

input {
  padding: 0 12px;
  border: 1px solid var(--lk-border);
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}

button {
  padding: 0 14px;
  border: 0;
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
}

.secondary {
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}
</style>
