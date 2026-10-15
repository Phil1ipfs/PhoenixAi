import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, streamMessage, type Conversation, type Health, type Message, type Settings, type WebMeta } from "./api";
import Composer from "./Composer";
import EmptyState from "./EmptyState";
import { IconArrowDown, IconMenu, IconSidebar } from "./Icons";
import MessageView from "./MessageView";
import SettingsPanel from "./SettingsPanel";
import Sidebar, { type View } from "./Sidebar";
import { useAppearance } from "./theme";
import { Banner, IconButton } from "./ui";

// Heavy views load on demand so the chat opens fast and uses less memory.
const Code = lazy(() => import("./Code"));
const Bench = lazy(() => import("./Bench"));

/** Only problems are surfaced; a healthy model shows nothing (the model is managed in Settings). */
function modelProblem(h: Health | null): { color: string; text: string } | null {
  if (!h) return { color: "bg-red-500", text: "Backend offline" };
  if (!h.ollama.connected) return { color: "bg-red-500", text: "Ollama offline" };
  if (!h.model_installed) return { color: "bg-amber-500", text: "Selected model isn’t installed — choose one in Settings" };
  return null;
}

function RamChip({ mb }: { mb: number | null | undefined }) {
  if (mb == null) return null;
  const color = mb < 800 ? "text-red-400" : mb < 1500 ? "text-amber-400" : "text-zinc-400";
  return <span className={`hidden rounded-full border border-zinc-800 px-2.5 py-1 text-xs sm:inline ${color}`} title="Free system RAM">{(mb / 1024).toFixed(1)} GB free</span>;
}

export default function App() {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [error, setError] = useState("");
  const [notices, setNotices] = useState<string[]>([]);
  const [statusMsg, setStatusMsg] = useState("");
  const [navOpen, setNavOpen] = useState(false);
  const [view, setView] = useState<View>(() => { try { return (localStorage.getItem("phoenix.view") as View) || "chat"; } catch { return "chat"; } });
  useEffect(() => { try { localStorage.setItem("phoenix.view", view); } catch { /* ignore */ } }, [view]);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const theme = useAppearance();
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem("phoenix.sidebar") === "collapsed"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem("phoenix.sidebar", collapsed ? "collapsed" : "open"); } catch { /* ignore */ } }, [collapsed]);

  const refreshHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth(null)), []);
  const refreshConvs = useCallback(() => api.conversations().then(setConvs).catch(() => {}), []);
  const refreshSettings = useCallback(() => api.settings().then(setSettings).catch(() => {}), []);

  useEffect(() => {
    refreshHealth(); refreshConvs(); refreshSettings();
    const t = setInterval(() => { if (!document.hidden) refreshHealth(); }, 15000); // no polling while the tab is hidden
    return () => clearInterval(t);
  }, [refreshHealth, refreshConvs, refreshSettings]);

  // Follow the stream only while the reader is at the bottom; scrolling up pauses it.
  useEffect(() => { const el = scrollRef.current; if (el && stickRef.current) el.scrollTop = el.scrollHeight; }, [messages]);
  const onScroll = () => {
    const el = scrollRef.current; if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickRef.current = near; setAtBottom(near);
  };
  const toBottom = () => { const el = scrollRef.current; if (!el) return; stickRef.current = true; el.scrollTo({ top: el.scrollHeight, behavior: "smooth" }); };

  const open = async (id: string) => {
    if (busy) return;
    setActiveId(id); setError(""); setNotices([]); setNavOpen(false); stickRef.current = true;
    try { setMessages((await api.conversation(id)).messages); } catch (e) { setError((e as Error).message); }
  };

  const newChat = async () => {
    if (busy) return;
    setView("chat");
    const c = await api.createConversation();
    setActiveId(c.id); setMessages([]); setError(""); setNotices([]); setNavOpen(false); refreshConvs();
  };

  const remove = async (id: string) => {
    if (busy) return;
    await api.deleteConversation(id);
    if (id === activeId) { setActiveId(null); setMessages([]); }
    refreshConvs();
  };

  const setWebMode = async (mode: Settings["web_mode"]) => {
    try { setSettings(await api.saveSettings({ web_mode: mode })); } catch (e) { setError((e as Error).message); }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setError(""); setNotices([]); setStatusMsg("");
    let id = activeId;
    if (!id) { const c = await api.createConversation(); id = c.id; setActiveId(id); }
    setInput(""); setBusy(true); stickRef.current = true;
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "" }]);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const patchLast = (fn: (m: Message) => Message) => setMessages((m) => [...m.slice(0, -1), fn(m[m.length - 1])]);
    try {
      await streamMessage(id, text, ctrl.signal, (e) => {
        if (e.type === "token") { setStatusMsg(""); patchLast((m) => ({ ...m, content: m.content + e.content })); }
        else if (e.type === "status") setStatusMsg(e.message);
        else if (e.type === "warning") setNotices((n) => [...n, e.message]);
        else if (e.type === "sources") patchLast((m) => ({ ...m, metrics: { ...m.metrics, web: { mode: "", needed: true, reasons: [], status: "sources_found", sources: e.sources } as WebMeta } }));
        else if (e.type === "done") patchLast(() => e.message);
        else if (e.type === "error") { setError(`${e.kind}: ${e.message}`); patchLast((m) => ({ ...m, error: e.message })); }
      });
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setBusy(false); setStatusMsg(""); abortRef.current = null;
      syncFromServer(id, ctrl.signal.aborted);
      refreshConvs(); refreshHealth();
    }
  };

  // After Stop the server persists the partial reply asynchronously, so retry briefly until it appears.
  const syncFromServer = async (id: string, expectPartial: boolean) => {
    for (let i = 0; i < (expectPartial ? 8 : 1); i++) {
      if (expectPartial) await new Promise((r) => setTimeout(r, 400));
      try {
        const c = await api.conversation(id);
        const last = c.messages[c.messages.length - 1];
        if (!expectPartial || last?.role === "assistant") { setMessages(c.messages); return; }
      } catch { /* retry */ }
    }
  };

  const stop = () => abortRef.current?.abort();
  const problem = modelProblem(health);
  const active = convs.find((c) => c.id === activeId);
  const lowRam = health?.memory.available_mb != null && health.memory.available_mb < 800;

  return (
    <div className="relative flex h-full overflow-hidden">
      <div className="ambient" aria-hidden="true"><i /><i /><i /></div>
      <Sidebar collapsed={collapsed} convs={convs} activeId={activeId} view={view} busy={busy} open={navOpen}
        problem={problem ? { ...problem, title: health?.ollama.error ?? problem.text } : null} themePref={theme.theme}
        onView={(v) => { setView(v); setNavOpen(false); }} onNew={newChat} onOpen={open} onDelete={remove}
        onSettings={() => { setShowSettings(true); setNavOpen(false); }} onClose={() => setNavOpen(false)} onTheme={theme.cycleTheme} />

      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <header className="glass flex h-12 shrink-0 items-center gap-2 border-b border-zinc-800 px-3 text-sm">
          <button className="press flex h-8 w-8 items-center justify-center rounded-lg text-zinc-400 hover:bg-zinc-800 md:hidden" onClick={() => setNavOpen(true)} aria-label="Open menu"><IconMenu /></button>
          <IconButton label={collapsed ? "Show sidebar" : "Hide sidebar"} onClick={() => setCollapsed((c) => !c)} className="max-md:hidden"><IconSidebar /></IconButton>
          <div className="min-w-0 flex-1 truncate font-medium text-zinc-100">{view === "chat" ? (active?.title ?? "New chat") : view === "code" ? "PHOENIX Code" : "Benchmark"}</div>
          <RamChip mb={health?.memory.available_mb} />
        </header>
        {lowRam && <div role="status" className="border-b border-amber-800/50 bg-amber-950/40 px-4 py-1.5 text-xs text-amber-200">Low free memory ({health?.memory.available_mb} MB). Replies may be slow — close other apps or set “Keep model in RAM” to unload in Settings.</div>}

        <Suspense fallback={<div className="mx-auto w-full max-w-3xl space-y-3 p-6" role="status" aria-label="Loading"><div className="skeleton h-5 w-1/3" /><div className="skeleton h-24 w-full" /><div className="skeleton h-24 w-full" /></div>}>
          {view === "code" && <Code busy={busy} setBusy={setBusy} />}
          {view === "bench" && <Bench busy={busy} setBusy={setBusy} />}
        </Suspense>

        <main className={`relative min-h-0 flex-1 flex-col ${view === "chat" ? "flex" : "hidden"}`}>
          <div ref={scrollRef} onScroll={onScroll} className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-6">
              {messages.length === 0 && <EmptyState health={health} onPick={setInput} />}
              {messages.map((m, i) => <MessageView key={m.id ?? `t${i}`} m={m} last={i === messages.length - 1} busy={busy} statusMsg={statusMsg} />)}
            </div>
          </div>
          {!atBottom && messages.length > 0 && (
            <button onClick={toBottom} aria-label="Scroll to latest" className="glass pop absolute bottom-28 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-zinc-700 text-zinc-300 shadow-lg transition hover:bg-zinc-800"><IconArrowDown size={16} /></button>
          )}
          {(notices.length > 0 || error) && (
            <div className="mx-auto w-full max-w-3xl space-y-1.5 px-4">
              {notices.map((n, i) => <Banner key={i} tone="warn" onDismiss={() => setNotices((l) => l.filter((_, j) => j !== i))}>{n}</Banner>)}
              {error && <Banner tone="error" onDismiss={() => setError("")}>{error}{/low_memory|out_of_memory/.test(error) ? " — try closing other applications." : ""}</Banner>}
            </div>
          )}
          <Composer value={input} busy={busy} webMode={settings?.web_mode} onChange={setInput} onSend={send} onStop={stop} onWebMode={setWebMode} />
        </main>
      </div>
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} onSaved={() => { refreshHealth(); refreshSettings(); }} />}
    </div>
  );
}
