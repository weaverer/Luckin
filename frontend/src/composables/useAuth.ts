import { ref } from "vue";

import { apiRequest } from "@/api/client/http";
import { WorkbenchApiError } from "@/api/client/errors";
import { useSessionStore } from "@/stores/session";

interface AuthSessionData {
  user: { user_id: string; username: string; display_name: string };
  csrf_token: string;
  expires_at: string;
}

function messageOf(error: unknown): string {
  return error instanceof WorkbenchApiError
    ? error.message
    : "暂时无法连接服务，请稍后重试";
}

export function useAuth() {
  const session = useSessionStore();
  const loading = ref(false);
  const errorMessage = ref("");

  function establish(data: AuthSessionData): void {
    session.establish(
      {
        userId: data.user.user_id,
        username: data.user.username,
        displayName: data.user.display_name,
      },
      data.csrf_token,
      data.expires_at,
    );
  }

  async function login(username: string, password: string): Promise<void> {
    loading.value = true;
    errorMessage.value = "";
    try {
      establish(
        await apiRequest<AuthSessionData>({
          method: "POST",
          url: "/auth/login",
          data: { username, password },
        }),
      );
    } catch (error) {
      errorMessage.value = messageOf(error);
      throw error;
    } finally {
      loading.value = false;
    }
  }

  async function restoreSession(): Promise<void> {
    try {
      establish(
        await apiRequest<AuthSessionData>({ method: "GET", url: "/auth/me" }),
      );
    } catch {
      session.clear();
    }
  }

  async function logout(): Promise<void> {
    try {
      await apiRequest<never>({ method: "POST", url: "/auth/logout" });
    } finally {
      session.clear();
    }
  }

  async function changePassword(
    currentPassword: string,
    newPassword: string,
  ): Promise<void> {
    loading.value = true;
    errorMessage.value = "";
    try {
      await apiRequest<never>({
        method: "PUT",
        url: "/auth/password",
        data: { current_password: currentPassword, new_password: newPassword },
      });
      session.clear();
    } catch (error) {
      errorMessage.value = messageOf(error);
      throw error;
    } finally {
      loading.value = false;
    }
  }

  return {
    session,
    loading,
    errorMessage,
    login,
    restoreSession,
    logout,
    changePassword,
  };
}
