import { useCallback, useEffect, useRef, useState } from "react";
import { codeApi, streamPost, type FileView, type Patch, type PatchSummary, type Project, type SearchHit, type StreamEvent, type TreeEntry } from "./api";
import DiffView from "./DiffView";
import Markdown from "./Markdown";

function TreeNode({ pid, entry, depth, onOpen, active }: { pid: string; entry: TreeEntry; depth: number; onOpen: (p: string) => void; active: string }) {
  const [open, setOpen] = useState(false);
  const [kids, setKids] = useState<TreeEntry[] | null>(null);
  const toggle = async () => {
    if (entry.type === "file") return onOpen(entry.path);
    if (!open && !kids) setKids(await codeApi.tree(pid, entry.path).catch(() => []));
    setOpen(!open);
  };
  return (
    <div>
      <div onClick={toggle} style={{ paddingLeft: depth * 12 + 6 }} data-path={entry.path}
        className={`cursor-pointer truncate py-0.5 text-sm hover:bg-zinc-800 ${active === entry.path ? "bg-zinc-800 text-orange-400" : "text-zinc-300"}`}>
        {entry.type === "dir" ? (open ? "▾ " : "▸ ") : "· "}{entry.name}
      </div>
      {open && kids?.map((k) => <TreeNode key={k.path} pid={pid} entry={k} depth={depth + 1} onOpen={onOpen} active={active} />)}
    </div>
  );
}

export default function Code({ busy, setBusy }: { busy: boolean; setBusy: (b: boolean) => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState<string>("");
  const [root, setRoot] = useState<TreeEntry[]>([]);
  const [file, setFile] = useState<FileView | null>(null);
  const [query, setQuery] = useState("");
  const [glob, setGlob] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [fileList, setFileList] = useState<string[] | null>(null);
  const [pathInput, setPathInput] = useState("");
  const [error, setError] = useState("");
  const [panel, setPanel] = useState<"assistant" | "changes">("assistant");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<{ path: string; start: number; end: number }[]>([]);
  const [instruction, setInstruction] = useState("");
  const [patch, setPatch] = useState<Patch | null>(null);
  const [history, setHistory] = useState<PatchSummary[]>([]);
  const [working, setWorking] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));
  const loadProjects = useCallback(async () => {
    const ps = await codeApi.projects().catch(() => []);
    setProjects(ps);
    setPid((cur) => cur || ps[0]?.id || "");
  }, []);
  useEffect(() => { loadProjects(); }, [loadProjects]);

  const refreshHistory = useCallback((id: string) => codeApi.patches(id).then(setHistory).catch(() => {}), []);
  useEffect(() => {
    if (!pid) { setRoot([]); return; }
    setFile(null); setHits(null); setFileList(null); setPatch(null); setAnswer(""); setError("");
    codeApi.tree(pid).then(setRoot).catch(fail);
    refreshHistory(pid);
  }, [pid, refreshHistory]);

  const addProject = async (path: string) => {
    setError("");
    try { const p = await codeApi.addProject(path); await loadProjects(); setPid(p.id); setPathInput(""); } catch (e) { fail(e); }
  };
  const pick = async () => {
    const { path } = await codeApi.pickFolder().catch(() => ({ path: null }));
    if (path) addProject(path); else setError("No folder chosen. You can also type a path below.");
  };
  const removeProject = async () => {
    if (!pid || !confirm("Remove this project from PHOENIX? (Your files are not touched.)")) return;
    await codeApi.removeProject(pid); setPid(""); await loadProjects();
  };
  const openFile = async (path: string) => {
    setError("");
    try { setFile(await codeApi.file(pid, path)); } catch (e) { setFile(null); fail(e); }
  };
  const runSearch = async () => {
    setError("");
    try {
      if (query.trim()) { setHits(await codeApi.search(pid, query.trim(), glob || undefined)); setFileList(null); }
      else if (glob.trim()) { setFileList((await codeApi.files(pid, glob.trim())).map((f) => f.path)); setHits(null); }
      else { setHits(null); setFileList(null); }
    } catch (e) { fail(e); }
  };

  const ask = async (mode: "explain" | "bugs" | "ask") => {
    if (busy) return;
    if (mode !== "ask" && !file) return setError("Open a file first.");
    setPanel("assistant"); setAnswer(""); setSources([]); setError(""); setBusy(true);
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      await streamPost<StreamEvent & { message?: { metrics?: { sources?: typeof sources } } }>(`/api/projects/${pid}/ask`,
        { question, mode, file: mode === "ask" ? undefined : file?.path }, ctrl.signal, (e) => {
          if (e.type === "token") setAnswer((a) => a + e.content);
          else if (e.type === "done") setSources(e.message.metrics?.sources ?? []);
          else if (e.type === "error") setError(`${e.kind}: ${e.message}`);
        });
    } catch (e) { if ((e as Error).name !== "AbortError") fail(e); } finally { setBusy(false); abortRef.current = null; }
  };

  const propose = async () => {
    if (!file || !instruction.trim()) return setError("Open a file and describe the change.");
    setError(""); setWorking("Asking the model for a patch (can take a minute on CPU)…"); setBusy(true);
    try {
      const p = await codeApi.generatePatch(pid, { path: file.path, instruction: instruction.trim() });
      setPatch(p); setPanel("changes"); refreshHistory(pid);
    } catch (e) { fail(e); } finally { setWorking(""); setBusy(false); }
  };
  const act = async (fn: (id: string) => Promise<Patch>, id: string) => {
    setError("");
    try {
      const p = await fn(id); setPatch(p); refreshHistory(pid);
      if (file) openFile(file.path);
    } catch (e) { fail(e); }
  };
  const openPatch = async (id: string) => { try { setPatch(await codeApi.patch(id)); } catch (e) { fail(e); } };

  const lines = file?.content.split("\n") ?? [];
  const statusColor: Record<string, string> = { pending: "text-amber-400", applied: "text-emerald-400", rejected: "text-zinc-500", rolled_back: "text-sky-400", conflict: "text-red-400" };

  return (
    <div className="flex h-full min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 p-2 text-sm">
        <select value={pid} onChange={(e) => setPid(e.target.value)} className="rounded border border-zinc-700 bg-zinc-900 px-2 py-1" aria-label="Project">
          {projects.length === 0 && <option value="">No project authorized</option>}
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <button onClick={pick} className="rounded bg-orange-600 px-2 py-1 hover:bg-orange-500">Choose folder…</button>
        <input value={pathInput} onChange={(e) => setPathInput(e.target.value)} placeholder="or paste a folder path" aria-label="Folder path"
          onKeyDown={(e) => e.key === "Enter" && pathInput && addProject(pathInput)} className="w-64 rounded border border-zinc-700 bg-zinc-900 px-2 py-1" />
        <button onClick={() => pathInput && addProject(pathInput)} className="rounded border border-zinc-700 px-2 py-1 hover:bg-zinc-800">Authorize</button>
        {pid && <button onClick={removeProject} className="ml-auto rounded px-2 py-1 text-zinc-500 hover:text-red-400">Remove project</button>}
      </div>
      {error && <div className="px-3 py-1 text-sm text-red-400" role="alert">⚠ {error}</div>}
      {!pid ? (
        <div className="m-auto max-w-md text-center text-zinc-500">
          <p>Authorize a project folder to start. PHOENIX can only read inside folders you choose here, never secrets (.env, keys), and never changes a file without your approval.</p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <div className="flex w-64 shrink-0 flex-col border-r border-zinc-800">
            <div className="space-y-1 p-2">
              <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runSearch()} placeholder="Search code…" aria-label="Search code"
                className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
              <input value={glob} onChange={(e) => setGlob(e.target.value)} onKeyDown={(e) => e.key === "Enter" && runSearch()} placeholder="Filter files: *.py, src/*" aria-label="File filter"
                className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto" data-testid="tree">
              {hits ? (
                <div>
                  <div className="px-2 text-xs text-zinc-500">{hits.length} match(es) <button className="underline" onClick={() => setHits(null)}>clear</button></div>
                  {hits.map((h, i) => <div key={i} onClick={() => openFile(h.path)} className="cursor-pointer px-2 py-1 text-xs hover:bg-zinc-800"><div className="text-orange-400">{h.path}:{h.line}</div><div className="truncate text-zinc-400">{h.text}</div></div>)}
                </div>
              ) : fileList ? (
                <div>
                  <div className="px-2 text-xs text-zinc-500">{fileList.length} file(s) <button className="underline" onClick={() => setFileList(null)}>clear</button></div>
                  {fileList.map((f) => <div key={f} onClick={() => openFile(f)} className="cursor-pointer truncate px-2 py-0.5 text-sm hover:bg-zinc-800">{f}</div>)}
                </div>
              ) : root.map((e) => <TreeNode key={e.path} pid={pid} entry={e} depth={0} onOpen={openFile} active={file?.path ?? ""} />)}
            </div>
          </div>

          <div className="flex min-w-0 flex-1 flex-col border-r border-zinc-800">
            {file ? (
              <>
                <div className="flex flex-wrap items-center gap-2 border-b border-zinc-800 px-3 py-1.5 text-sm">
                  <span className="font-mono text-zinc-300">{file.path}</span>
                  <span className="text-xs text-zinc-600">{file.total_lines} lines{file.truncated ? " (truncated view)" : ""}</span>
                  <button disabled={busy} onClick={() => ask("explain")} className="ml-auto rounded bg-zinc-800 px-2 py-0.5 hover:bg-zinc-700 disabled:opacity-40">Explain</button>
                  <button disabled={busy} onClick={() => ask("bugs")} className="rounded bg-zinc-800 px-2 py-0.5 hover:bg-zinc-700 disabled:opacity-40">Find bugs</button>
                </div>
                <pre className="min-h-0 flex-1 overflow-auto p-2 text-xs leading-5" data-testid="file-view">
                  {lines.map((l, i) => <div key={i} className="flex"><span className="w-10 shrink-0 select-none pr-2 text-right text-zinc-600">{file.start + i}</span><span className="whitespace-pre">{l}</span></div>)}
                </pre>
              </>
            ) : <div className="m-auto text-sm text-zinc-600">Select a file from the tree or search results.</div>}
          </div>

          <div className="flex w-[30rem] max-w-[45%] shrink-0 flex-col">
            <div className="flex border-b border-zinc-800 text-sm">
              {(["assistant", "changes"] as const).map((t) => (
                <button key={t} onClick={() => setPanel(t)} className={`flex-1 py-1.5 ${panel === t ? "border-b-2 border-orange-500 text-white" : "text-zinc-500"}`}>
                  {t === "assistant" ? "Assistant" : `Changes${history.some((h) => h.status === "pending") ? " ●" : ""}`}
                </button>
              ))}
            </div>
            {panel === "assistant" ? (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="min-h-0 flex-1 overflow-y-auto p-3 text-sm" data-testid="answer">
                  {answer ? <Markdown text={answer} /> : <p className="text-zinc-600">{busy ? "Thinking…" : "Use Explain / Find bugs on the open file, or ask a question about the project. Only small, relevant excerpts are sent to the model."}</p>}
                  {sources.length > 0 && <div className="mt-3 border-t border-zinc-800 pt-2 text-xs text-zinc-500">Context used: {sources.map((s) => `${s.path}:${s.start}-${s.end}`).join(", ")}</div>}
                </div>
                <div className="flex gap-2 border-t border-zinc-800 p-2">
                  <input value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => e.key === "Enter" && question.trim() && ask("ask")} placeholder="Ask about this project…" aria-label="Project question"
                    className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
                  {busy ? <button onClick={() => abortRef.current?.abort()} className="rounded bg-red-600 px-3 text-sm">■ Stop</button>
                    : <button onClick={() => ask("ask")} disabled={!question.trim()} className="rounded bg-orange-600 px-3 text-sm disabled:opacity-40">Ask</button>}
                </div>
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 flex-col">
                <div className="space-y-1 border-b border-zinc-800 p-2">
                  <div className="text-xs text-zinc-500">Propose a change to {file ? <span className="font-mono">{file.path}</span> : "the open file"} — nothing is written until you approve.</div>
                  <div className="flex gap-2">
                    <input value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="Describe the change…" aria-label="Change instruction"
                      className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm" />
                    <button onClick={propose} disabled={busy || !file} className="rounded bg-orange-600 px-3 text-sm disabled:opacity-40">Propose</button>
                  </div>
                  {working && <div className="animate-pulse text-xs text-zinc-500">{working}</div>}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto p-2">
                  {patch && (
                    <div data-testid="patch">
                      <div className="mb-1 flex items-center gap-2 text-sm">
                        <span className="font-mono">{patch.path}</span>
                        <span className={statusColor[patch.status]} data-testid="patch-status">{patch.status}</span>
                        {patch.is_new_file && <span className="text-xs text-sky-400">new file</span>}
                      </div>
                      {patch.meta && (
                        <div className="mb-2 rounded border border-zinc-800 bg-zinc-900/60 p-2 text-xs text-zinc-400" data-testid="patch-meta">
                          <div>Strategy: <b className="text-zinc-300">{patch.meta.strategy}</b> · attempts: <b className="text-zinc-300">{patch.meta.attempts}</b></div>
                          {patch.meta.checks.map((c) => <div key={c.name}>{c.ok ? "✓" : c.ok === false ? "✗" : "–"} {c.name.replace(/_/g, " ")}{c.detail ? ` (${c.detail})` : ""}</div>)}
                          {(patch.meta.attempt_log ?? []).filter((a) => !a.ok).map((a) => <div key={a.n} className="text-amber-300">retry {a.n}: {a.error}</div>)}
                        </div>
                      )}
                      <DiffView rows={patch.rows} />
                      <div className="mt-2 flex gap-2">
                        {patch.status === "pending" && <>
                          <button onClick={() => act(codeApi.apply, patch.id)} className="rounded bg-emerald-600 px-3 py-1 text-sm hover:bg-emerald-500">Approve &amp; apply</button>
                          <button onClick={() => act(codeApi.reject, patch.id)} className="rounded border border-zinc-600 px-3 py-1 text-sm hover:bg-zinc-800">Reject</button>
                        </>}
                        {patch.status === "applied" && <button onClick={() => act(codeApi.rollback, patch.id)} className="rounded border border-sky-600 px-3 py-1 text-sm text-sky-300 hover:bg-zinc-800">Roll back</button>}
                      </div>
                      {patch.note && <div className="mt-1 text-xs text-red-400">{patch.note}</div>}
                    </div>
                  )}
                  <div className="mt-4 text-xs uppercase tracking-wide text-zinc-600">Change history</div>
                  {history.length === 0 && <p className="text-xs text-zinc-600">No changes yet.</p>}
                  {history.map((h) => (
                    <div key={h.id} onClick={() => openPatch(h.id)} className={`cursor-pointer border-b border-zinc-900 py-1 text-xs hover:bg-zinc-900 ${patch?.id === h.id ? "bg-zinc-900" : ""}`}>
                      <span className={statusColor[h.status]}>{h.status}</span> <span className="font-mono text-zinc-300">{h.path}</span>
                      <div className="truncate text-zinc-500">{h.title} · {h.created_at.slice(0, 19).replace("T", " ")}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
