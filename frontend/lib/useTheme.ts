"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "sightline-theme";

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

// Matches the default applied server-side (before the beforeInteractive
// theme script runs) so there is nothing to reconcile post-hydration beyond
// the one-time sync useSyncExternalStore already handles for us.
function getServerSnapshot(): Theme {
  return "light";
}

/** Reads/writes the `data-theme` attribute set by the inline theme-init
 * script (see app/layout.tsx), staying in sync via useSyncExternalStore
 * rather than mirroring external DOM state into local component state. */
export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = useCallback(() => {
    const next: Theme = getSnapshot() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore storage errors (e.g. private browsing)
    }
  }, []);

  return [theme, toggle];
}
