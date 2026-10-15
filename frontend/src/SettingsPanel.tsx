import { useEffect, useState } from "react";
import { api, type ModelInfo, type Settings } from "./api";
import { useAppearance, type Accent, type ThemePref } from "./theme";
import { Sheet, btnGhost, btnPrimary, inputCls } from "./ui";

const field = inputCls;
const lbl = "mb-1 block text-xs font-medium text-zinc-400";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <fieldset className="mt-5 rounded-2xl border border-zinc-800 bg-zinc-900/40 p-4 pt-2"><legend className="px-2 text-xs font-semibold uppercase tracking-wider text-orange-500">{title}</legend>{children}</fieldset>;
}

const THEMES: { id: ThemePref; label: string }[] = [{ id: "system", label: "System" }, { id: "dark", label: "Dark" }, { id: "light", label: "Light" }];
const ACCENTS: { id: Accent; label: string; css: string }[] = [
  { id: "ember", label: "Ember", css: "linear-gradient(135deg,#ff5a1f,#ffa31a)" },
  { id: "electric", label: "Electric", css: "linear-gradient(135deg,#3b82f6,#8b5cf6)" },
  { id: "violet", label: "Violet", css: "linear-gradient(135deg,#8b5cf6,#d946ef)" },
];

function Appearance() {
  const a = useAppearance();
  return (
    <Section title="Appearance">
      <div className={lbl} id="s-theme-l">Theme</div>
      <div role="radiogroup" aria-labelledby="s-theme-l" className="grid grid-cols-3 gap-1 rounded-xl bg-zinc-950/60 p-1">
        {THEMES.map((t) => <button key={t.id} role="radio" aria-checked={a.theme === t.id} onClick={() => a.setTheme(t.id)}
          className={`press rounded-lg py-1.5 text-sm ${a.theme === t.id ? "bg-zinc-800 text-zinc-50 shadow-sm" : "text-zinc-500 hover:text-zinc-200"}`}>{t.label}</button>)}
      </div>
      <div className={`${lbl} mt-3`} id="s-acc-l">Accent</div>
      <div role="radiogroup" aria-labelledby="s-acc-l" className="flex gap-2">
        {ACCENTS.map((c) => <button key={c.id} role="radio" aria-checked={a.accent === c.id} onClick={() => a.setAccent(c.id)}
          className={`press flex items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm ${a.accent === c.id ? "border-orange-500 text-zinc-50" : "border-zinc-700 text-zinc-400 hover:text-zinc-200"}`}>
          <span className="h-3.5 w-3.5 rounded-full" style={{ background: c.css }} />{c.label}</button>)}
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={a.ambient} onChange={(e) => a.setAmbient(e.target.checked)} /> Ambient background glow</label>
      <p className="mt-1 text-xs text-zinc-500">Appearance is stored in this browser only. Animations are disabled automatically if your system asks for reduced motion.</p>
    </Section>
  );
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
  if (!s || !loaded) return <Sheet title="Settings" onClose={onClose}>err ? <p role="alert" className="pt-5 text-sm text-red-400">{err}</p> : <div className="space-y-3 pt-5" role="status" aria-label="Loading settings"><div className="skeleton h-4 w-1/4" /><div className="skeleton h-20 w-full" /><div className="skeleton h-4 w-1/3" /><div className="skeleton h-32 w-full" /></div></Sheet>;
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
    try { await api.saveSettings({ web_contact: s.web_contact, web_timeout_s: s.web_timeout_s, wikimedia_pages: s.wikimedia_pages }); const r = await api.webTest(); setWebTest(r.ok ? `✓ Reachable (${r.provider})` : `✗ ${r.error}`); }
    catch (e) { setWebTest("✗ " + (e as Error).message); }
  };
  return (
    <Sheet title="Settings" onClose={onClose} footer={<>
      <button onClick={onClose} className={btnGhost}>Cancel</button>
      <button onClick={save} className={btnPrimary}>Save</button>
    </>}>

      <Appearance />

      <Section title="Model">
        <div className="mb-2 flex items-center gap-1.5 text-xs text-emerald-400"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />Local · runs on this PC through Ollama (no cloud models configured)</div>
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
        <p className="mt-1 text-xs text-zinc-500">Added to PHOENIX’s User-Agent. Leave blank to stay anonymous: Wikipedia is then used for search snippets only. Wikimedia’s policy asks automated clients to identify an operator, so reading full Wikipedia pages needs both this contact and the switch below. PHOENIX never works around that restriction.</p>
        <label className="mt-2 flex items-center gap-2 text-sm text-zinc-300"><input id="s-wiki" type="checkbox" checked={s.wikimedia_pages} onChange={(e) => set("wikimedia_pages", e.target.checked)} /> Allow reading full Wikipedia pages (requires a contact above; off by default)</label>
        <div className="mt-2 flex items-center gap-3 text-sm"><button onClick={testWeb} className={btnGhost}>Test connection</button><span className="text-xs text-zinc-400" data-testid="web-test">{webTest}</span></div>
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
        <div className="mt-3 flex items-center gap-3 text-sm"><button onClick={unload} className={btnGhost}>Unload model now</button><span className="text-xs text-zinc-400" data-testid="unload-msg">{unloadMsg}</span></div>
        <p className="mt-1 text-xs text-zinc-500">Measured on this PC: the model holds ~2.1 GB; a context of 4096 costs only ~70 MB more than 2048. CPU thread count made no reliable difference (±10% noise), so it stays automatic.</p>
      </Section>

      {err && <p role="alert" className="mt-3 text-sm text-red-400">{err}</p>}
    </Sheet>
  );
}
