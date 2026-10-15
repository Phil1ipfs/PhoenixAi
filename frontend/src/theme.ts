import { useSyncExternalStore } from "react";

export type ThemePref = "system" | "dark" | "light";
export type Accent = "ember" | "electric" | "violet";
export interface Appearance { theme: ThemePref; accent: Accent; ambient: boolean }

const KEY = "phoenix.appearance";
const LEGACY_THEME = "phoenix.theme";
const DEFAULTS: Appearance = { theme: "system", accent: "ember", ambient: true };

function load(): Appearance {
  try {
    const raw = localStorage.getItem(KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
    const legacy = localStorage.getItem(LEGACY_THEME);
    if (legacy === "dark" || legacy === "light" || legacy === "system") return { ...DEFAULTS, theme: legacy };
  } catch { /* storage unavailable or corrupt: use defaults */ }
  return DEFAULTS;
}

export function resolveTheme(pref: ThemePref): "dark" | "light" {
  return pref === "system" ? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark") : pref;
}

let state = load();
const listeners = new Set<() => void>();

function apply() {
  const el = document.documentElement;
  el.setAttribute("data-theme", resolveTheme(state.theme));
  el.setAttribute("data-accent", state.accent);
  el.setAttribute("data-ambient", state.ambient ? "on" : "off");
}

function set(patch: Partial<Appearance>) {
  state = { ...state, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(state)); localStorage.setItem(LEGACY_THEME, state.theme); } catch { /* ignore */ }
  apply();
  listeners.forEach((l) => l());
}

if (typeof document !== "undefined") {
  apply();
  matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => { if (state.theme === "system") apply(); });
}

const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };
const snapshot = () => state;

/** Shared appearance store: the sidebar toggle and the Settings panel stay in sync. */
export function useAppearance() {
  const a = useSyncExternalStore(subscribe, snapshot);
  return {
    ...a,
    setTheme: (theme: ThemePref) => set({ theme }),
    cycleTheme: () => set({ theme: a.theme === "system" ? "dark" : a.theme === "dark" ? "light" : "system" }),
    setAccent: (accent: Accent) => set({ accent }),
    setAmbient: (ambient: boolean) => set({ ambient }),
  };
}
