import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { setCsrfToken } from "@/api/client/http";

export interface SessionUser {
  userId: string;
  username: string;
  displayName: string;
}

export const useSessionStore = defineStore("session", () => {
  const user = ref<SessionUser | null>(null);
  const initialized = ref(false);
  const csrfToken = ref<string | null>(null);
  const expiresAt = ref<string | null>(null);
  const authenticated = computed(() => user.value !== null);

  function setUser(value: SessionUser | null): void {
    user.value = value;
    initialized.value = true;
  }

  function establish(value: SessionUser, csrf: string, expires: string): void {
    user.value = value;
    csrfToken.value = csrf;
    expiresAt.value = expires;
    initialized.value = true;
    setCsrfToken(csrf);
  }

  function clear(): void {
    user.value = null;
    csrfToken.value = null;
    expiresAt.value = null;
    initialized.value = true;
    setCsrfToken(null);
  }

  return {
    user,
    initialized,
    csrfToken,
    expiresAt,
    authenticated,
    setUser,
    establish,
    clear,
  };
});
