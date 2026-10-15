import { useEffect, useState } from "react";
import { api, type ModelInfo, type Settings } from "./api";

export function Modal({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div role="dialog" aria-label="Settings" className="max-h-[90vh] w-[620px] max-w-[95vw] overflow-y-auto rounded-xl border border-zinc-700 bg-zinc-950 p-5" onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  );
}

const field = "w-full rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm outline-none focus:border-orange-600";
const lbl = "mb-1 block text-xs text-zinc-400";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <fieldset className="mt-4 border-t border-zinc-800 pt-3"><legend className="pr-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">{title}</legend>{children}</fieldset>;
}

export default function SettingsPanel({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const [s, setS] = useState<Settings | null>(null);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [err, setErr] = useState("");
  const [webTest, setWebTest] = useState("");
  const [unloadMsg, setUnloadMsg] = useState("");
  useEffect(() => {
    api.settings().then(setS).catch((e) => setErr(String(e.message)));
    api.models().then((m) => { setModels(m.models); setLoaded(true); }).catch(() => { setLoaded(true); setErr("Could not list models (is Ollama running?)"); });
  }, []);
  if (!s || !loaded) return <Modal onClose={onClose}>{err || "Loading…"}</Modal>;
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const num = (k: keyof Settings) => (e: React.ChangeEvent<HTMLInputElement>) => set(k, Number(e.target.value) as never);
  const names = models.map((m) => m.name);
  const save = async () => {
    try { await api.saveSettings(s); onSaved(); onClose(); } catch (e) { setErr((e as Error).message); }
  };
  const unload = async () => {
    setUnloadMsg("Unloading…");
    try { const r = await api.unloadModel(); setUnloadMsg(`✓ ${r.unloaded} unloaded; RAM freed`); onSaved(); } catch (e) { setUnloadMsg("✗ " + (e as Error).message); }
  };
  const testWeb = async () => {
    setWebTest("Testing…");
    try { await api.saveSettings({ web_contact: s.web_contact, web_timeout_s: s.web_timeout_s }); const r = await api.webTest(); setWebTest(r.ok ? `✓ Reachable (${r.provider})` : `✗ ${r.error}`); }
    catch (e) { setWebTest("✗ " + (e as Error).message); }
  };
  return (
    <Modal onClose={onClose}>
      <h2 className="text-lg font-semibold">Settings</h2>

      <Section title="Model">
        <label htmlFor="s-model" className={lbl}>Model (installed in Ollama)</label>
        <select id="s-model" className={field} value={s.model} onChange={(e) => set("model", e.target.value)}>
          {!names.includes(s.model) && <option value={s.model}>{s.model} (not installed)</option>}
          {models.map((m) => <option key={m.name} value={m.name}>{m.name}{m.parameter_size ? ` · ${m.parameter_size}` : ""}</option>)}
        </select>
        <label htmlFor="s-sys" className={`${lbl} mt-3`}>System instructions</label>
        <textarea id="s-sys" className={field} rows={3} value={s.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
      </Section>

      <Section title="Internet research (optional)">
        <label htmlFor="s-web" className={lbl}>Internet access</label>
        <select id="s-web" className={field} value={s.web_mode} onChange={(e) => set("web_mode", e.target.value as Settings["web_mode"])}>
          <option value="off">Off — never contact the internet (default)</option>
          <option value="auto">Auto — search only for time-sensitive questions</option>
          <option value="always">Always — search for every chat message</option>
        </select>
        <p className="mt-1 text-xs text-zinc-500">Uses DuckDuckGo and Wikipedia (free, no API key). Search queries and page requests leave this PC; nothing else does. Web results are untrusted and are cited as numbered sources.</p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div><label htmlFor="s-ws" className={lbl}>Max sources</label><input id="s-ws" className={field} type="number" min={1} max={6} value={s.web_max_sources} onChange={num("web_max_sources")} /></div>
          <div><label htmlFor="s-wt" className={lbl}>Web timeout (s)</label><input id="s-wt" className={field} type="number" min={2} max={30} value={s.web_timeout_s} onChange={num("web_timeout_s")} /></div>
        </div>
        <label htmlFor="s-wc" className={`${lbl} mt-3`}>Web contact (optional email or URL)</label>
        <input id="s-wc" className={field} value={s.web_contact} placeholder="you@example.com" onChange={(e) => set("web_contact", e.target.value)} />
        <p className="mt-1 text-xs text-zinc-500">Added to PHOENIX’s User-Agent. Wikipedia refuses requests that don’t identify an operator, so without this its pages can’t be read (only search snippets are used). Leave blank to stay anonymous.</p>
        <div className="mt-2 flex items-center gap-3 text-sm"><button onClick={testWeb} className="rounded-md border border-zinc-700 px-3 py-1 hover:bg-zinc-800">Test connection</button><span className="text-xs text-zinc-400" data-testid="web-test">{webTest}</span></div>
      </Section>

      <Section title="Generation">
        <div className="grid grid-cols-2 gap-3">
          <div><label htmlFor="s-ctx" className={lbl}>Context (num_ctx)</label><input id="s-ctx" className={field} type="number" value={s.num_ctx} onChange={num("num_ctx")} /></div>
          <div><label htmlFor="s-temp" className={lbl}>Temperature</label><input id="s-temp" className={field} type="number" step="0.1" value={s.temperature} onChange={num("temperature")} /></div>
          <div><label htmlFor="s-np" className={lbl}>Max output tokens</label><input id="s-np" className={field} type="number" value={s.num_predict} onChange={num("num_predict")} /></div>
          <div><label htmlFor="s-to" className={lbl}>Timeout (s)</label><input id="s-to" className={field} type="number" value={s.timeout_s} onChange={num("timeout_s")} /></div>
          <div><label htmlFor="s-mp" className={lbl}>Max prompt chars</label><input id="s-mp" className={field} type="number" value={s.max_prompt_chars} onChange={num("max_prompt_chars")} /></div>
          <div><label htmlFor="s-url" className={lbl}>Ollama URL (local only)</label><input id="s-url" className={field} value={s.ollama_url} onChange={(e) => set("ollama_url", e.target.value)} /></div>
        </div>
      </Section>

      <Section title="Performance & memory">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor="s-ka" className={lbl}>Keep model in RAM after a reply</label>
            <select id="s-ka" className={field} value={s.keep_alive} onChange={(e) => set("keep_alive", e.target.value)}>
              {["0", "1m", "5m", "30m"].map((v) => <option key={v} value={v}>{v === "0" ? "Unload immediately (frees ~2 GB)" : `${v}${v === "5m" ? " (default)" : ""}`}</option>)}
              {!["0", "1m", "5m", "30m"].includes(s.keep_alive) && <option value={s.keep_alive}>{s.keep_alive}</option>}
            </select>
          </div>
          <div><label htmlFor="s-th" className={lbl}>CPU threads (0 = automatic)</label><input id="s-th" className={field} type="number" min={0} max={32} value={s.num_thread} onChange={num("num_thread")} /></div>
          <div><label htmlFor="s-mf" className={lbl}>Warn below free RAM (MB)</label><input id="s-mf" className={field} type="number" value={s.min_free_mb} onChange={num("min_free_mb")} /></div>
          <label className="flex items-end gap-2 pb-1.5 text-sm"><input type="checkbox" checked={s.memory_guard} onChange={(e) => set("memory_guard", e.target.checked)} /> Memory guard</label>
        </div>
        <div className="mt-3 flex items-center gap-3 text-sm"><button onClick={unload} className="rounded-md border border-zinc-700 px-3 py-1 hover:bg-zinc-800">Unload model now</button><span className="text-xs text-zinc-400" data-testid="unload-msg">{unloadMsg}</span></div>
        <p className="mt-1 text-xs text-zinc-500">Measured on this PC: the model holds ~2.1 GB; a context of 4096 costs only ~70 MB more than 2048. CPU thread count made no reliable difference (±10% noise), so it stays automatic.</p>
      </Section>

      {err && <p role="alert" className="mt-3 text-sm text-red-400">{err}</p>}
      <div className="sticky -bottom-5 -mx-5 mt-5 flex justify-end gap-2 border-t border-zinc-800 bg-zinc-950 px-5 py-3">
        <button onClick={onClose} className="rounded-md px-3 py-1.5 text-sm hover:bg-zinc-800">Cancel</button>
        <button onClick={save} className="rounded-md bg-orange-600 px-4 py-1.5 text-sm font-medium hover:bg-orange-500">Save</button>
      </div>
    </Modal>
  );
}
