<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";

import AppSidebar from "./AppSidebar.vue";
import AppTopbar from "./AppTopbar.vue";

const mobileMenu = window.matchMedia("(max-width: 620px)");
const menuCollapsed = ref(mobileMenu.matches);
const collapseOnNarrowScreen = (event: MediaQueryListEvent): void => {
  if (event.matches) menuCollapsed.value = true;
};

onMounted(() => mobileMenu.addEventListener("change", collapseOnNarrowScreen));
onBeforeUnmount(() =>
  mobileMenu.removeEventListener("change", collapseOnNarrowScreen),
);
</script>

<template>
  <div class="app-shell" :class="{ 'sidebar-collapsed': menuCollapsed }">
    <AppSidebar v-model="menuCollapsed" />
    <main class="app-content">
      <AppTopbar
        :menu-collapsed="menuCollapsed"
        @toggle-menu="menuCollapsed = !menuCollapsed"
      />
      <RouterView />
    </main>
  </div>
</template>
