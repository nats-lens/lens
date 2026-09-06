import { lazy, Suspense, type ComponentType } from "react";
import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { AppFrame } from "@/components";

/** Every screen is its own chunk.
 *
 * The shell, the design tokens and the API client are what the first paint
 * needs; the nine screens are not, and shipping all of them to someone who
 * opened one is the difference between a fast load and a slow one. The design
 * system page in particular is a development reference that no user visits.
 *
 * Each import names the screen's own file rather than its folder. A barrel would
 * be tidier to read and worse to ship: the chunk takes its name from the module,
 * so every screen came out as `index-<hash>.js`, and anything else the barrel
 * re-exported was dragged into the chunk along with the screen.
 */
const screen = (load: () => Promise<Record<string, unknown>>, name: string) => {
  const Lazy = lazy(async () => ({ default: (await load())[name] as ComponentType }));
  return () => <Lazy />;
};

/** The frame renders once and stays.
 *
 * Everything that is not the current screen -- sidebar, server switcher, the
 * registry and health reads behind them -- lives above the `Outlet`, so moving
 * between screens swaps only the content column. One `Suspense` here, rather
 * than one per route, is what keeps a lazy chunk from blanking the sidebar too.
 *
 * No spinner: a chunk arrives in a frame or two on a local connection, and a
 * flash of "loading" is more disruptive than the pause it papers over.
 */
const rootRoute = createRootRoute({
  component: () => (
    <AppFrame>
      <Suspense fallback={null}>
        <Outlet />
      </Suspense>
    </AppFrame>
  ),
});

/** The nine screens of the design.
 *
 * Written out one by one rather than generated from a list: TanStack Router
 * derives every `<Link to="...">` type from these literal paths, so a helper
 * that takes `path: string` would widen them and turn the whole app's
 * navigation back into untyped strings.
 *
 */
const servers = createRoute({ getParentRoute: () => rootRoute, path: "/", component: screen(() => import("@/features/servers/ServersScreen"), "ServersScreen") });
const addServer = createRoute({ getParentRoute: () => rootRoute, path: "/servers/new", component: screen(() => import("@/features/connection/AddServerScreen"), "AddServerScreen") });
const editServer = createRoute({
  getParentRoute: () => rootRoute,
  path: "/servers/$serverId/edit",
  component: screen(() => import("@/features/connection/AddServerScreen"), "EditServerScreen"),
});
const monitor = createRoute({ getParentRoute: () => rootRoute, path: "/monitor", component: screen(() => import("@/features/monitor/MonitorScreen"), "MonitorScreen") });
const jetstream = createRoute({ getParentRoute: () => rootRoute, path: "/jetstream", component: screen(() => import("@/features/jetstream/JetStreamScreen"), "JetStreamScreen") });
const kv = createRoute({ getParentRoute: () => rootRoute, path: "/kv", component: screen(() => import("@/features/kv/KeyValueScreen"), "KeyValueScreen") });
const objects = createRoute({ getParentRoute: () => rootRoute, path: "/objects", component: screen(() => import("@/features/objects/ObjectStoreScreen"), "ObjectStoreScreen") });

const core = createRoute({ getParentRoute: () => rootRoute, path: "/core", component: screen(() => import("@/features/core/CoreScreen"), "CoreScreen") });
const advisories = createRoute({
  getParentRoute: () => rootRoute,
  path: "/advisories",
  component: screen(() => import("@/features/advisories/AdvisoriesScreen"), "AdvisoriesScreen"),
});
const schemas = createRoute({
  getParentRoute: () => rootRoute,
  path: "/schemas",
  component: screen(() => import("@/features/schemas/SchemasScreen"), "SchemasScreen"),
});
const settings = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  component: screen(() => import("@/features/settings/SettingsScreen"), "SettingsScreen"),
});
const designSystem = createRoute({
  getParentRoute: () => rootRoute,
  path: "/design-system",
  component: screen(() => import("@/components/DesignSystem"), "DesignSystem"),
});

export const router = createRouter({
  routeTree: rootRoute.addChildren([
    servers,
    addServer,
    editServer,
    monitor,
    jetstream,
    kv,
    objects,
    core,
    advisories,
    schemas,
    settings,
    designSystem,
  ]),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
