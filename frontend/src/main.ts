import { createApp } from "vue";
import "primeicons/primeicons.css";

import App from "./App.vue";
import { installAppProviders } from "./app/providers";
import { router } from "./app/router";
import { useAuth } from "./composables/useAuth";
import { useThemeStore } from "./stores/theme";
import "./styles/tokens.css";
import "./styles/reset.css";
import "./styles/app.css";

const app = createApp(App);

installAppProviders(app);
useThemeStore().initialize();

async function bootstrap(): Promise<void> {
  await useAuth().restoreSession();
  app.use(router);
  await router.isReady();
  app.mount("#app");
}

void bootstrap();
