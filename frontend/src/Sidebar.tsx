import { useMemo, useState } from "react";
import type { Conversation } from "./api";
import { IconChat, IconCheck, IconClose, IconCode, IconGauge, IconMonitor, IconMoon, IconPlus, IconSearch, IconSettings, IconSun, IconTrash, Logo } from "./Icons";
import { IconButton } from "./ui";
import type { ThemePref } from "./theme";

export type View = "chat" | "code" | "bench";

const MODES: { id: View; label: string; icon: typeof IconChat }[] = [
  { id: "chat", label: "Chat", icon: IconChat },
  { id: "code", label: "Code", icon: IconCode },
  { id: "bench", label: "Benchmark", icon: IconGauge },
];

const HINTS: Record<Exclude<View, "chat">, string> = {
  code: "Authorize a project, then explain, search and edit code. Edits always need your approval.",
  bench: "Measure the selected model on fixed coding tasks.",
};

function groupOf(iso: string, now: number): string {
  const t = Date.parse(iso.endsWith("Z") || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
  if (Number.isNaN(t)) return "Older";
  const d = new Date(now); d.setHours(0, 0, 0, 0);
  const day = 86400000;
  if (t >= d.getTime()) return "Today";
  if (t >= d.getTime() - day) return "Yesterday";
  if (t >= d.getTime() - 7 * day) return "Previous 7 days";
  return "Older";
}
const ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

interface Props {
  convs: Conversation[]; activeId: string | null; view: View; busy: boolean; open: boolean; collapsed: boolean;
  problem: { color: string; text: string; title: string } | null; themePref: ThemePref;
  onView: (v: View) => void; onNew: () => void; onOpen: (id: string) => void; onDelete: (id: string) => void;
  onSettings: () => void; onClose: () => void; onTheme: () => void;
}

export default function Sidebar(p: Props) {
  const [q, setQ] = useState("");
  const [confirming, setConfirming] = useState<string | null>(null);

  const groups = useMemo(() => {
    const now = Date.now();
    const needle = q.trim().toLowerCase();
    const map = new Map<string, Conversation[]>();
    for (const c of p.convs) {
      if (needle && !c.title.toLowerCase().includes(needle)) continue;
      const g = groupOf(c.updated_at, now);
      map.set(g, [...(map.get(g) ?? []), c]);
    }
    return ORDER.filter((g) => map.has(g)).map((g) => [g, map.get(g)!] as const);
  }, [p.convs, q]);

  const ThemeIcon = p.themePref === "light" ? IconSun : p.themePref === "dark" ? IconMoon : IconMonitor;

  return (
    <>
      {p.open && <div className="fixed inset-0 z-20 bg-black/50 backdrop-blur-sm md:hidden" onClick={p.onClose} aria-hidden="true" />}
      <aside aria-label="Sidebar"
        className={`glass fixed inset-y-0 left-0 z-30 flex w-72 shrink-0 flex-col border-r border-zinc-800 transition-[transform,margin,visibility] duration-200 ease-out md:static md:translate-x-0 ${p.collapsed ? "md:invisible md:-ml-72" : "md:visible md:ml-0"} ${p.open ? "visible translate-x-0" : "invisible -translate-x-full"}`}>
        <div className="flex items-center justify-between px-4 pb-2 pt-4">
          <div className="flex items-center gap-2.5">
            <Logo size={28} live busy={p.busy} />
            <span className="text-[15px] font-bold tracking-wide text-zinc-50">PHOENIX <span className="ember-text">AI</span></span>
          </div>
          <IconButton label="Close menu" onClick={p.onClose} className="md:hidden"><IconClose /></IconButton>
        </div>

        <div className="px-3 pt-2">
          <button onClick={p.onNew} disabled={p.busy} aria-label="New chat"
            className="press flex w-full items-center gap-2 rounded-xl border border-zinc-700 bg-zinc-800/60 px-3 py-2 text-sm font-medium text-zinc-100 transition hover:border-orange-500/60 hover:bg-zinc-800 disabled:opacity-40">
            <IconPlus size={16} className="text-orange-500" /> New chat
          </button>
        </div>

        {p.view === "chat" ? (
          <>
            <div className="px-3 pt-3"><div className="relative">
              <IconSearch size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-500" />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats" aria-label="Search chats"
                className="w-full rounded-lg border border-transparent bg-zinc-800/50 py-1.5 pl-8 pr-2 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-orange-500" />
            </div></div>
            <nav className="mt-2 min-h-0 flex-1 overflow-y-auto px-2 pb-2" aria-label="Conversations">
              {groups.map(([g, list]) => (
                <div key={g} className="mb-2">
                  <div className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500">{g}</div>
                  {list.map((c) => {
                    const active = c.id === p.activeId;
                    return (
                      <div key={c.id} data-testid="conv-row"
                        className={`group relative mb-0.5 flex items-center rounded-lg text-sm transition-colors ${active ? "bg-zinc-800 text-zinc-50" : "text-zinc-300 hover:bg-zinc-800/60"}`}>
                        {active && <span className="ember-bg absolute left-0 top-2 h-[calc(100%-1rem)] w-0.5 rounded-full" />}
                        <button onClick={() => p.onOpen(c.id)} title={c.title} aria-current={active ? "true" : undefined} className="min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-left">{c.title}</button>
                        {confirming === c.id ? (
                          <span className="flex shrink-0 items-center pr-1">
                            <IconButton label="Confirm delete" onClick={() => { setConfirming(null); p.onDelete(c.id); }} className="h-7 w-7 text-red-400 hover:text-red-300"><IconCheck size={15} /></IconButton>
                            <IconButton label="Cancel delete" onClick={() => setConfirming(null)} className="h-7 w-7"><IconClose size={15} /></IconButton>
                          </span>
                        ) : (
                          <IconButton label="Delete" onClick={() => setConfirming(c.id)} disabled={p.busy}
                            className="mr-1 h-7 w-7 opacity-100 hover:text-red-400 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100"><IconTrash size={15} /></IconButton>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}
              {groups.length === 0 && <p className="px-3 pt-3 text-xs text-zinc-500">{q ? "No chats match your search." : "No conversations yet."}</p>}
            </nav>
          </>
        ) : <div className="min-h-0 flex-1 px-5 pt-5 text-xs leading-relaxed text-zinc-500">{HINTS[p.view]}</div>}

        <div className="space-y-2 border-t border-zinc-800 p-3">
          <div role="tablist" aria-label="Mode" className="grid grid-cols-3 gap-1 rounded-xl bg-zinc-950/60 p-1">
            {MODES.map(({ id, label, icon: Icon }) => (
              <button key={id} role="tab" aria-selected={p.view === id} onClick={() => p.onView(id)}
                className={`press flex flex-col items-center gap-0.5 rounded-lg py-1.5 text-[11px] font-medium transition ${p.view === id ? "bg-zinc-800 text-zinc-50 shadow-sm" : "text-zinc-500 hover:text-zinc-200"}`}>
                <Icon size={17} className={p.view === id ? "text-orange-500" : ""} />{label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 px-1 text-xs text-zinc-400">
            {p.problem ? <div role="status" title={p.problem.title} className="flex min-w-0 flex-1 items-center gap-2"><span className={`h-2 w-2 shrink-0 rounded-full ${p.problem.color}`} /><span className="min-w-0 flex-1 truncate">{p.problem.text}</span></div> : <span className="flex-1" />}
            <IconButton label={`Theme: ${p.themePref}`} onClick={p.onTheme} className="h-7 w-7"><ThemeIcon size={15} /></IconButton>
            <IconButton label="Settings" onClick={p.onSettings} className="h-7 w-7"><IconSettings size={15} /></IconButton>
          </div>
        </div>
      </aside>
    </>
  );
}
