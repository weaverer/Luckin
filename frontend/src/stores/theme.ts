import { defineStore } from "pinia";
import { computed, ref } from "vue";

export const useThemeStore = defineStore("theme", () => {
  const mode = ref<"light" | "dark">("light");
  const isDark = computed(() => mode.value === "dark");

  function apply(next: "light" | "dark"): void {
    mode.value = next;
    document.documentElement.classList.toggle("app-dark", next === "dark");
    localStorage.setItem("lucking-theme", next);
  }

  function initialize(): void {
    const saved = localStorage.getItem("lucking-theme");
    const systemDark =
      window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
    apply(
      saved === "dark" || (saved === null && systemDark) ? "dark" : "light",
    );
  }

  function toggle(): void {
    apply(isDark.value ? "light" : "dark");
  }

  return { mode, isDark, initialize, toggle };
});
