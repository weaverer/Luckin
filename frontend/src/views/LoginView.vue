<script setup lang="ts">
import { reactive } from "vue";
import { useRoute, useRouter } from "vue-router";

import AppSurface from "@/components/common/AppSurface.vue";
import { useAuth } from "@/composables/useAuth";

const auth = useAuth();
const router = useRouter();
const route = useRoute();
const form = reactive({ username: "", password: "" });
const fieldErrors = reactive({ username: "", password: "" });

async function submit(): Promise<void> {
  fieldErrors.username =
    form.username.trim().length >= 3 ? "" : "请输入至少 3 个字符的账号";
  fieldErrors.password = form.password ? "" : "请输入密码";
  if (fieldErrors.username || fieldErrors.password) return;
  try {
    await auth.login(form.username, form.password);
    await router.replace(
      typeof route.query.redirect === "string" ? route.query.redirect : "/",
    );
  } catch {
    // useAuth exposes the safe user-facing message.
  }
}
</script>

<template>
  <main class="login-page">
    <AppSurface class="login-card">
      <div class="brand"><span aria-hidden="true" /> Lucking</div>
      <div>
        <p class="eyebrow">投资工作台</p>
        <h1>登录</h1>
        <p class="muted">使用管理员为你预置的账号继续。</p>
      </div>
      <form :aria-busy="auth.loading.value" novalidate @submit.prevent="submit">
        <div class="field">
          <label for="username">账号</label>
          <input
            id="username"
            v-model="form.username"
            name="username"
            autocomplete="username"
            :aria-invalid="Boolean(fieldErrors.username)"
          />
          <small v-if="fieldErrors.username">{{ fieldErrors.username }}</small>
        </div>
        <div class="field">
          <label for="password">密码</label>
          <input
            id="password"
            v-model="form.password"
            name="password"
            type="password"
            autocomplete="current-password"
            :aria-invalid="Boolean(fieldErrors.password)"
          />
          <small v-if="fieldErrors.password">{{ fieldErrors.password }}</small>
        </div>
        <p v-if="auth.errorMessage.value" class="form-error" role="alert">
          {{ auth.errorMessage.value }}
        </p>
        <button type="submit" :disabled="auth.loading.value">
          {{ auth.loading.value ? "登录中…" : "登录" }}
        </button>
      </form>
    </AppSurface>
  </main>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: grid;
  place-items: center;
  padding: 20px;
  background: radial-gradient(
    circle at 85% 12%,
    color-mix(in srgb, var(--lk-primary) 16%, transparent),
    transparent 35%
  );
}

.login-card {
  display: grid;
  width: min(440px, 100%);
  gap: 28px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-weight: 750;
}

.brand span {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--lk-fortune);
}

.eyebrow,
h1,
p {
  margin: 0;
}

h1 {
  margin-top: 8px;
  font-size: 2.5rem;
}

form,
.field {
  display: grid;
  gap: 10px;
}

form {
  gap: 18px;
}

label {
  font-weight: 650;
}

input {
  min-height: 44px;
  padding: 0 13px;
  border: 1px solid var(--lk-border);
  border-radius: 10px;
  color: var(--lk-text);
  background: var(--lk-surface-soft);
}

small,
.form-error {
  color: var(--lk-danger);
}

button {
  min-height: 44px;
  border: 0;
  border-radius: 11px;
  color: var(--lk-primary-contrast);
  background: var(--lk-primary);
  cursor: pointer;
}
</style>
