import { useEffect, useRef, useState } from "react";
import { benchApi, streamPost, type BenchEvent, type BenchResult, type BenchSummary } from "./api";

const LABELS: Record<string, string> = { codegen: "Code generation", bugfix: "Bug fixing", comprehension: "Project comprehension", tool: "Patch tasks (original 3)", tool_heldout: "Patch tasks (held-out 8)" };

export default function Bench({ busy, setBusy }: { busy: boolean; setBusy: (b: boolean) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [quick, setQuick] = useState(true);
  const [strategy, setStrategy] = useState("auto");
  const [live, setLive] = useState<BenchResult[]>([]);
  const [current, setCurrent] = useState("");
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<BenchSummary | null>(null);
  const [past, setPast] = useState<{ id: number; model: string; created_at: string; results: BenchSummary }[]>([]);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  const loadPast = () => benchApi.history().then(setPast).catch(() => {});
  useEffect(() => { loadPast(); }, []);

  const run = async () => {
    setError(""); setLive([]); setSummary(null); setBusy(true);
    const ctrl = new AbortController(); abortRef.current = ctrl;
    try {
      await streamPost<BenchEvent>("/api/benchmark/run", { confirm_execute: true, limit: quick ? 1 : undefined, strategy }, ctrl.signal, (e) => {
        if (e.type === "start") setTotal(e.total);
        else if (e.type === "task_start") setCurrent(e.id);
        else if (e.type === "task_done") setLive((l) => [...l, e.result]);
        else if (e.type === "summary") setSummary(e.summary);
        else if (e.type === "error") setError(`${e.kind}: ${e.message}`);
      });
    } catch (e) { if ((e as Error).name !== "AbortError") setError((e as Error).message); }
    finally { setBusy(false); setCurrent(""); abortRef.current = null; loadPast(); }
  };

  const running = busy && !summary;
  const shown = summary ?? (running ? null : past[0]?.results ?? null);
  return (
    <div className="min-w-0 flex-1 overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-4">
        <h1 className="text-xl font-semibold ember-text">Local coding benchmark</h1>
        <p className="text-sm text-zinc-400">Runs fixed tasks against the <b>selected model</b>. Python answers are checked by executing real tests. It runs the model’s code in a restricted subprocess (import allowlist, timeout) — best-effort hardening, not a security sandbox — so it only runs when you confirm below. Expect several minutes on CPU.</p>
        <div className="flex flex-wrap items-center gap-4 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" checked={confirm} onChange={(e) => setConfirm(e.target.checked)} /> I allow PHOENIX to execute model-generated test code</label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={quick} onChange={(e) => setQuick(e.target.checked)} /> Quick run (1 task per category)</label>
          <label className="flex items-center gap-2">Patch strategy
            <select aria-label="Patch strategy" value={strategy} onChange={(e) => setStrategy(e.target.value)} className="rounded-md border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs">
              <option value="auto">auto (current product)</option><option value="symbol">symbol</option><option value="search_replace">search/replace + retries</option><option value="legacy">legacy (single try)</option>
            </select></label>
          {busy && abortRef.current
            ? <button onClick={() => abortRef.current?.abort()} className="rounded-lg bg-red-600 text-white px-3 py-1">Stop</button>
            : <button onClick={run} disabled={!confirm || busy} className="rounded ember-bg text-white px-4 py-1 font-medium disabled:opacity-40">Run benchmark</button>}
        </div>
        {error && <div className="text-sm text-red-400" role="alert">{error}</div>}
        {busy && <div className="text-sm text-zinc-400" data-testid="bench-progress">Running {current || "…"} ({live.length}/{total || "?"})</div>}
        {(live.length > 0 || shown) && (
          <>
          {!summary && !running && shown && <div className="text-xs text-zinc-500">Showing the last saved run.</div>}
          <div className="overflow-x-auto rounded-2xl border border-zinc-800"><table className="w-full text-sm [&_td]:px-3 [&_th]:px-3 [&_th]:py-2 [&_th]:font-medium" data-testid="bench-results">
            <thead><tr className="bg-zinc-900 text-left text-zinc-500"><th>Task</th><th>Result</th><th>Latency</th><th>tok/s</th><th>Detail</th></tr></thead>
            <tbody>
              {(live.length ? live : shown?.results ?? []).map((r) => (
                <tr key={r.id} className="border-t border-zinc-800">
                  <td className="py-1 font-mono text-xs">{r.id}</td>
                  <td className={r.passed ? "text-emerald-400" : "text-red-400"}>{r.passed ? "PASS" : "FAIL"}</td>
                  <td>{r.latency_ms ? (r.latency_ms / 1000).toFixed(1) + " s" : "–"}</td>
                  <td>{r.tokens_per_sec ?? "–"}</td>
                  <td className="max-w-xs truncate text-xs text-zinc-500" title={r.detail}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
          </>
        )}
        {shown && (
          <div className="rounded-lg border border-zinc-800 p-3 text-sm" data-testid="bench-summary">
            <div className="mb-2 text-zinc-400">Model <b className="text-zinc-200">{shown.model}</b> · {shown.finished_at.slice(0, 19).replace("T", " ")}</div>
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              {Object.entries(shown.categories).map(([c, v]) => <div key={c}>{LABELS[c] ?? c}: <b>{v.passed}/{v.total}</b></div>)}
              <div>Patch strategy: <b>{shown.tool_strategy ?? "–"}</b></div>
              <div>Patch format valid: <b>{shown.tool_format_success.ok}/{shown.tool_format_success.total}</b></div>
              <div>Avg speed: <b>{shown.avg_tokens_per_sec ?? "–"} tok/s</b></div>
              <div>Avg / max latency: <b>{shown.avg_latency_s ?? "–"} s / {shown.max_latency_s ?? "–"} s</b></div>
              <div>Model RAM: <b>{shown.model_ram_mb ?? "–"} MB</b></div>
              <div>Lowest free RAM during run: <b>{shown.min_available_ram_mb ?? "–"} MB</b></div>
            </div>
            <p className="mt-2 text-xs text-zinc-600">Small task counts: treat percentages as indicative, not statistically meaningful.</p>
          </div>
        )}
      </div>
    </div>
  );
}
