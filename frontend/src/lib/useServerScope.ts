/** Which registered server the rest of the app is looking at.
 *
 * The shared switcher store every screen needs -- Core, JetStream, KV, the
 * object store and Monitor all show one "current server" in the sidebar and
 * scope every query to it. It is a store rather than per-screen state precisely
 * so the choice survives navigating between them.
 *
 * Persisted as a plain id string under one key, deliberately not JSON, so a
 * screen that only needs the id can read `localStorage` directly if it ever
 * has to run before this module does.
 */
import { useMemo } from "react";
import { create } from "zustand";
import { useQuery } from "@tanstack/react-query";
import { apiQuery } from "@/lib/api";
import type { ShellServer } from "@/components";

const STORAGE_KEY = "nats-lens.selected-server";

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(id: string | null): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Private browsing or a full quota: the pick just won't survive a reload.
  }
}

interface ScopeState {
  manualId: string | null;
  select: (id: string | null) => void;
}

const useScopeStore = create<ScopeState>((set) => ({
  manualId: readStored(),
  select: (id) => {
    writeStored(id);
    set({ manualId: id });
  },
}));

/** The registry, plus which one of it is "current", for any screen to share.
 *
 * Defaults to the first connected server rather than the first registered one --
 * a registered-but-unreachable server would otherwise fail every query the
 * moment a screen mounted.
 */
export function useServerScope() {
  const serversQuery = useQuery(apiQuery("/api/servers"));
  const servers = useMemo(() => serversQuery.data ?? [], [serversQuery.data]);

  const manualId = useScopeStore((s) => s.manualId);
  const select = useScopeStore((s) => s.select);

  const serverId = useMemo(() => {
    if (manualId && servers.some((s) => s.id === manualId)) return manualId;
    return servers.find((s) => s.state === "connected")?.id ?? servers[0]?.id ?? null;
  }, [manualId, servers]);

  // Rebuilt only when the chosen server actually changes. It is a new object
  // otherwise, and the sidebar switcher would re-render on every consumer's
  // render rather than on a real change of server or state.
  const shellServer = useMemo<ShellServer | null>(() => {
    const current = servers.find((s) => s.id === serverId);
    return current
      ? { id: current.id, name: current.name, url: current.primary_url, state: current.state }
      : null;
  }, [servers, serverId]);

  return {
    servers,
    serverId,
    selectServer: select,
    shellServer,
    isLoading: serversQuery.isLoading,
    isError: serversQuery.isError,
    error: serversQuery.error,
  };
}
