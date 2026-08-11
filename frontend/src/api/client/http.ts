import axios, { AxiosError, type AxiosRequestConfig } from "axios";

import { isErrorEnvelope, WorkbenchApiError } from "./errors";

export interface ApiEnvelope<T> {
  code: 0;
  message: "";
  data: T;
  errors: [];
  request_id: string;
  timestamp: string;
}

let csrfToken: string | null = null;

export function setCsrfToken(value: string | null): void {
  csrfToken = value;
}

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "/api/v1",
  timeout: 15_000,
  withCredentials: true,
  headers: { Accept: "application/json" },
});

http.interceptors.request.use((config) => {
  const method = config.method?.toUpperCase();
  if (csrfToken && method && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    config.headers.set("X-CSRF-Token", csrfToken);
  }
  return config;
});

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<unknown>) => {
    if (error.response && isErrorEnvelope(error.response.data)) {
      throw new WorkbenchApiError(error.response.status, error.response.data);
    }
    throw error;
  },
);

export async function apiRequest<T>(config: AxiosRequestConfig): Promise<T> {
  const response = await http.request<ApiEnvelope<T>>(config);
  if (response.status === 204) return undefined as T;
  if (response.data.code !== 0) throw new Error("成功 HTTP 响应包含非零业务码");
  return response.data.data;
}
