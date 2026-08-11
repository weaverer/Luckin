<script setup lang="ts">
import { navigationItems } from "@/app/navigation";

const collapsed = defineModel<boolean>({ required: true });
</script>

<template>
  <aside
    class="app-navigation-sidebar"
    :class="{ collapsed }"
    aria-label="主导航"
  >
    <header class="sidebar-header">
      <RouterLink class="brand" :to="{ name: 'dashboard' }" title="Lucking">
        <span class="brand-mark" aria-hidden="true" />
        <span class="collapsible-copy">Lucking</span>
      </RouterLink>
      <button
        class="collapse-button"
        type="button"
        :aria-label="collapsed ? '展开主菜单' : '折叠主菜单'"
        :title="collapsed ? '展开主菜单' : '折叠主菜单'"
        @click="collapsed = !collapsed"
      >
        <i
          class="pi"
          :class="collapsed ? 'pi-angle-right' : 'pi-angle-left'"
          aria-hidden="true"
        />
      </button>
    </header>
    <div class="sidebar-content">
      <p class="nav-caption collapsible-copy">工作区</p>
      <nav>
        <RouterLink
          v-for="item in navigationItems.filter(
            (entry) => entry.routeName !== 'account',
          )"
          :key="item.routeName"
          :to="{ name: item.routeName }"
          :title="collapsed ? item.label : undefined"
        >
          <i class="pi" :class="item.icon" aria-hidden="true" />
          <span class="collapsible-copy">
            <b>{{ item.label }}</b>
            <small>{{ item.description }}</small>
          </span>
        </RouterLink>
      </nav>
      <RouterLink
        class="account"
        :to="{ name: 'account' }"
        :title="collapsed ? '账号设置' : undefined"
      >
        <i class="pi pi-user" aria-hidden="true" />
        <span class="collapsible-copy"
          ><b>账号设置</b><small>密码与登录安全</small></span
        >
      </RouterLink>
    </div>
  </aside>
</template>

<style scoped>
.app-navigation-sidebar {
  position: sticky;
  top: 16px;
  display: flex;
  min-width: 0;
  height: calc(100vh - 32px);
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--lk-border);
  border-radius: 18px;
  color: var(--lk-text);
  background: var(--lk-sidebar-bg);
}
.sidebar-header {
  display: flex;
  min-height: 70px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--lk-border);
}
.sidebar-content {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1;
  padding: 12px;
}

.brand,
nav a,
.account {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--lk-text-secondary);
  text-decoration: none;
}

.brand {
  min-width: 0;
  padding: 4px;
  color: var(--lk-text);
  font-size: 1.25rem;
  font-weight: 750;
}
.collapse-button {
  display: grid;
  width: 44px;
  min-width: 44px;
  height: 44px;
  place-items: center;
  border: 1px solid var(--lk-border);
  border-radius: 9px;
  color: var(--lk-text-secondary);
  background: var(--lk-surface);
  cursor: pointer;
}
.nav-caption {
  margin: 8px 14px 12px;
  color: var(--lk-text-muted);
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.brand-mark {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--lk-fortune);
  box-shadow: 0 0 0 5px color-mix(in srgb, var(--lk-fortune) 18%, transparent);
}

nav {
  display: grid;
  gap: 6px;
}

nav a,
.account {
  min-height: 58px;
  padding: 10px 14px;
  border-radius: 12px;
}
nav a > i,
.account > i {
  width: 22px;
  min-width: 22px;
  font-size: 1.05rem;
  text-align: center;
}
nav a > span,
.account > span {
  display: grid;
  gap: 3px;
}
nav b,
.account b {
  font-size: 0.9rem;
}
nav small,
.account small {
  color: var(--lk-text-muted);
  font-size: 0.7rem;
}

nav a:hover,
.account:hover {
  background: var(--lk-surface-hover);
}

nav a.router-link-active {
  color: var(--lk-text);
  background: var(--lk-selection);
  box-shadow: inset 3px 0 var(--lk-fortune);
}

.account {
  margin-top: auto;
}
.collapsible-copy {
  min-width: 0;
  opacity: 1;
  transition: opacity 120ms ease;
}
.collapsed .sidebar-header {
  justify-content: center;
  padding-inline: 8px;
}
.collapsed .brand {
  display: none;
}
.collapsed .sidebar-content {
  padding-inline: 8px;
}
.collapsed .collapsible-copy {
  display: none;
  opacity: 0;
}
.collapsed nav a,
.collapsed .account {
  justify-content: center;
  padding-inline: 8px;
}
@media (max-width: 840px) {
  .sidebar-header {
    justify-content: center;
    padding-inline: 8px;
  }
  .brand,
  .collapsible-copy {
    display: none;
  }
  .sidebar-content {
    padding-inline: 8px;
  }
  nav a,
  .account {
    justify-content: center;
    padding-inline: 8px;
  }
}
@media (max-width: 620px) {
  .app-navigation-sidebar {
    position: fixed;
    inset: 8px auto 8px 8px;
    z-index: 40;
    width: 72px;
    height: auto;
    box-shadow: var(--lk-shadow);
  }
  .app-navigation-sidebar.collapsed {
    display: none;
  }
}
@media (prefers-reduced-motion: reduce) {
  .collapsible-copy {
    transition: none;
  }
}
</style>
