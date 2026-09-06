/** Which theme the app is wearing.
 *
 * Three settings, two appearances: `system` is a *preference*, not a look, and
 * it resolves to light or dark from the operating system. The resolved value is
 * what reaches the DOM -- `data-theme` is always a concrete `light` or `dark`,
 * never `system` -- so the stylesheet needs one block per appearance rather than
 * a `prefers-color-scheme` copy of every token.
 */
import { useCallback, useEffect, useState } from "react";

export type ThemeSetting = "system" | "light" | "dark";
export type Appearance = "light" | "dark";

const STORAGE_KEY = "nats-lens.theme";

/** The ground colour of each appearance, kept in step with `index.css`.
 *
 * Used for the `theme-color` meta so the browser's own chrome matches the page
 * instead of flashing the other theme's colour on load.
 */
const GROUND: Record<Appearance, string> = {
  dark: "#0c0b0a",
  light: "#faf9f7",
};

export function readSetting(): ThemeSetting {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark" || stored === "system") return stored;
  } catch {
    // Private browsing, or storage disabled. The system default still works.
  }
  return "system";
}

export function systemAppearance(): Appearance {
  return typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: light)").matches
    ? "light"
    : "dark";
}

export function resolve(setting: ThemeSetting): Appearance {
  return setting === "system" ? systemAppearance() : setting;
}

/** Stamp the appearance onto the document. Safe to call before React mounts. */
export function apply(appearance: Appearance): void {
  document.documentElement.setAttribute("data-theme", appearance);
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", GROUND[appearance]);
}

export function useTheme() {
  const [setting, setSetting] = useState<ThemeSetting>(readSetting);
  const [appearance, setAppearance] = useState<Appearance>(() => resolve(readSetting()));

  useEffect(() => {
    const next = resolve(setting);
    setAppearance(next);
    apply(next);
  }, [setting]);

  // Following the system means following it as it changes, not only at load.
  useEffect(() => {
    if (setting !== "system") return;
    const query = window.matchMedia("(prefers-color-scheme: light)");
    const onChange = () => {
      const next = systemAppearance();
      setAppearance(next);
      apply(next);
    };
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [setting]);

  const choose = useCallback((next: ThemeSetting) => {
    setSetting(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // The choice just will not survive a reload.
    }
  }, []);

  return { setting, appearance, choose };
}
