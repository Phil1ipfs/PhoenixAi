import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { api, streamMessage, type Conversation, type Health, type Message, type Settings, type WebMeta } from "./api";
import Markdown from "./Markdown";
import SettingsPanel from "./SettingsPanel";
import { SourceList, WebStatus, linkCitations } from "./Sources";

// Heavy views load on demand so the chat opens fast and uses less memory.
const Code = lazy(() => import("./Code"));
const Bench = lazy(() => import("./Bench"));

type View = "chat" | "code" | "bench";

function modelState(h: Health | null): { color: string; text: string } {
  if (!h) return { color: "bg-red-500", text: "Backend offline" };
  if (!h.ollama.connected) return { color: "bg-red-500", text: "Ollama offline" };
  if (!h.model_installed) return { color: "bg-amber-500", text: `${h.active_model} not installed` };
  const inMem = h.loaded?.some((m) => m.name === h.active_model);
  return { color: "bg-emerald-500", text: `${h.active_model} ready${inMem ? " · in memory" : ""}` };
}

function RamChip({ mb }: { mb: number | null | undefined }) {
  if (mb == null) return null;
  const color = mb < 800 ? "text-red-400" : mb < 1500 ? "text-amber-400" : "text-zinc-500";
  return <span className={`text-xs ${color}`} title="Free system RAM">{(mb / 1024).toFixed(1)} GB free</span>;
}

function Dots() {
  return <span className="inline-flex gap-1" aria-label="working"><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500" /><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500 [animation-delay:150ms]" /><i className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500 [animation-delay:300ms]" /></span>;
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
  const bottomRef = useRef<HTMLDivElement>(null);

  const refreshHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth(null)), []);
  const refreshConvs = useCallback(() => api.conversations().then(setConvs).catch(() => {}), []);
  const refreshSettings = useCallback(() => api.settings().then(setSettings).catch(() => {}), []);

  useEffect(() => {
    refreshHealth(); refreshConvs(); refreshSettings();
    const t = setInterval(() => { if (!document.hidden) refreshHealth(); }, 15000); // no polling while the tab is hidden
    return () => clearInterval(t);
  }, [refreshHealth, refreshConvs, refreshSettings]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ block: "end" }); }, [messages]);

  const open = async (id: string) => {
    if (busy) return;
    setActiveId(id); setError(""); setNotices([]); setNavOpen(false);
    try { setMessages((await api.conversation(id)).messages); } catch (e) { setError((e as Error).message); }
  };

  const newChat = async () => {
    if (busy) return;
    const c = await api.createConversation();
    setActiveId(c.id); setMessages([]); setError(""); setNotices([]); setNavOpen(false); refreshConvs();
  };

  const remove = async (id: string) => {
    if (busy || !confirm("Delete this conversation?")) return;
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
    setInput(""); setBusy(true);
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
  const ms = modelState(health);
  const active = convs.find((c) => c.id === activeId);
  const lowRam = health?.memory.available_mb != null && health.memory.available_mb < 800;

  return (
    <div className="flex h-full">
      <aside className={`${navOpen ? "flex" : "hidden"} absolute inset-y-0 left-0 z-20 w-64 shrink-0 flex-col border-r border-zinc-800 bg-zinc-950 md:static md:flex`}>
        <div className="p-3">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-lg font-bold tracking-wide text-orange-500">🔥 PHOENIX AI</div>
            <button className="text-zinc-500 md:hidden" onClick={() => setNavOpen(false)} aria-label="Close menu">✕</button>
          </div>
          <div className="mb-3 flex gap-1 rounded-lg bg-zinc-900 p-1 text-xs" role="tablist">
            {([["chat", "Chat"], ["code", "Code"], ["bench", "Benchmark"]] as const).map(([v, label]) => (
              <button key={v} role="tab" aria-selected={view === v} onClick={() => { setView(v); setNavOpen(false); }}
                className={`flex-1 rounded-md py-1 ${view === v ? "bg-zinc-700 font-medium text-white" : "text-zinc-400 hover:text-zinc-200"}`}>{label}</button>
            ))}
          </div>
          {view === "chat" && <button onClick={newChat} disabled={busy} className="w-full rounded-md bg-orange-600 py-1.5 text-sm font-medium hover:bg-orange-500 disabled:opacity-50">+ New conversation</button>}
        </div>
        <nav className="flex-1 overflow-y-auto px-2" aria-label="Conversations" hidden={view !== "chat"}>
          {convs.map((c) => (
            <div key={c.id} onClick={() => open(c.id)} className={`group mb-0.5 flex cursor-pointer items-center justify-between rounded-md px-2 py-1.5 text-sm ${c.id === activeId ? "bg-zinc-800 text-white" : "text-zinc-300 hover:bg-zinc-900"}`}>
              <span className="truncate" title={c.title}>{c.title}</span>
              <button onClick={(e) => { e.stopPropagation(); remove(c.id); }} className="hidden text-zinc-500 hover:text-red-400 group-hover:block" title="Delete">✕</button>
            </div>
          ))}
          {convs.length === 0 && <p className="px-2 text-xs text-zinc-600">No conversations yet.</p>}
        </nav>
        {view !== "chat" && <div className="flex-1 px-4 text-xs text-zinc-600">{view === "code" ? "Authorize a project, then explain, search and edit code. Edits always need your approval." : "Measure the selected model on fixed coding tasks."}</div>}
        <div className="space-y-2 border-t border-zinc-800 p-3">
          <div className="flex items-center gap-2 text-xs text-zinc-400" title={health?.ollama.error ?? ""}><span className={`h-2 w-2 shrink-0 rounded-full ${ms.color}`} /><span className="truncate">{ms.text}</span></div>
          <button onClick={() => setShowSettings(true)} className="w-full rounded-md border border-zinc-700 py-1 text-sm hover:bg-zinc-800">⚙ Settings</button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-2 text-sm">
          <button className="text-zinc-400 md:hidden" onClick={() => setNavOpen(true)} aria-label="Open menu">☰</button>
          <div className="min-w-0 flex-1 truncate font-medium text-zinc-200">{view === "chat" ? (active?.title ?? "New chat") : view === "code" ? "PHOENIX Code" : "Benchmark"}</div>
          {view === "chat" && settings && (
            <label className="flex items-center gap-1 text-xs text-zinc-400" title="Internet research">
              🌐
              <select aria-label="Web mode" value={settings.web_mode} onChange={(e) => setWebMode(e.target.value as Settings["web_mode"])} className="rounded-md border border-zinc-700 bg-zinc-900 px-1.5 py-1 text-xs">
                <option value="off">Web: off</option><option value="auto">Web: auto</option><option value="always">Web: always</option>
              </select>
            </label>
          )}
          <RamChip mb={health?.memory.available_mb} />
        </header>
        {lowRam && <div role="status" className="border-b border-amber-800/50 bg-amber-950/40 px-4 py-1 text-xs text-amber-200">Low free memory ({health?.memory.available_mb} MB). Replies may be slow — close other apps or set “Keep model in RAM” to unload in Settings.</div>}

        <Suspense fallback={<div className="m-auto text-sm text-zinc-500">Loading…</div>}>
          {view === "code" && <Code busy={busy} setBusy={setBusy} />}
          {view === "bench" && <Bench busy={busy} setBusy={setBusy} />}
        </Suspense>

        <main className={`min-h-0 flex-1 flex-col ${view === "chat" ? "flex" : "hidden"}`}>
          <div className="flex-1 overflow-y-auto">
            <div className="mx-auto max-w-3xl space-y-5 px-4 py-6">
              {messages.length === 0 && (
                <div className="mt-20 text-center text-zinc-500">
                  <div className="text-4xl">🔥</div>
                  <p className="mt-3 text-base text-zinc-300">What are we building?</p>
                  <p className="mt-1 text-sm">Runs locally on your machine. Turn on <b>Web: auto</b> to verify time-sensitive questions against live sources.</p>
                  {health && !health.ollama.connected && <p className="mt-3 text-red-400">Ollama isn’t reachable. Start it, then reload.</p>}
                </div>
              )}
              {messages.map((m, i) => {
                const last = i === messages.length - 1;
                const web = m.metrics?.web;
                return (
                  <div key={m.id ?? `t${i}`} className={m.role === "user" ? "flex justify-end" : ""}>
                    <div className={m.role === "user" ? "max-w-[85%] whitespace-pre-wrap rounded-2xl bg-zinc-800 px-4 py-2 leading-relaxed" : "w-full"}>
                      {m.role === "assistant" && <div className="mb-1 text-xs font-semibold tracking-wide text-orange-500">PHOENIX</div>}
                      {m.role === "user" ? m.content
                        : m.content ? <Markdown text={linkCitations(m.content, web?.sources)} />
                        : busy && last ? <span className="flex items-center gap-2 text-sm text-zinc-500"><Dots />{statusMsg || "Thinking… (the first reply can take a while while the model loads)"}</span> : null}
                      {m.role === "assistant" && <SourceList web={web} />}
                      {m.role === "assistant" && !(busy && last) && <WebStatus web={web} />}
                      {m.role === "assistant" && m.metrics?.tokens_per_sec !== undefined && (
                        <div className="mt-1 text-xs text-zinc-600">
                          {m.model} · {m.metrics.tokens_per_sec ?? "?"} tok/s · {((m.metrics.wall_ms ?? 0) / 1000).toFixed(1)}s{m.metrics.status === "stopped" ? " · stopped" : ""}
                        </div>
                      )}
                      {m.role === "assistant" && m.metrics?.status === "stopped" && m.metrics?.tokens_per_sec === undefined && <div className="mt-1 text-xs text-zinc-600">{m.model} · stopped</div>}
                    </div>
                  </div>
                );
              })}
              <div ref={bottomRef} />
            </div>
          </div>
          <div className="mx-auto w-full max-w-3xl space-y-1 px-4">
            {notices.map((n, i) => <div key={i} role="status" className="rounded-md bg-amber-950/40 px-3 py-1 text-xs text-amber-200">⚠ {n}</div>)}
            {error && <div role="alert" className="rounded-md bg-red-950/40 px-3 py-1 text-sm text-red-300">⚠ {error}{/low_memory|out_of_memory/.test(error) ? " — try closing other applications." : ""}</div>}
          </div>
          <div className="border-t border-zinc-800 p-3">
            <div className="mx-auto flex max-w-3xl gap-2">
              <textarea
                value={input} onChange={(e) => setInput(e.target.value)} rows={2} placeholder="Message PHOENIX… (Enter to send, Shift+Enter for newline)"
                onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                className="flex-1 resize-none rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-orange-600"
              />
              {busy
                ? <button onClick={stop} className="rounded-lg bg-red-600 px-4 text-sm font-medium hover:bg-red-500">■ Stop</button>
                : <button onClick={send} disabled={!input.trim()} className="rounded-lg bg-orange-600 px-4 text-sm font-medium hover:bg-orange-500 disabled:opacity-40">Send</button>}
            </div>
          </div>
        </main>
      </div>
      {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} onSaved={() => { refreshHealth(); refreshSettings(); }} />}
    </div>
  );
}
