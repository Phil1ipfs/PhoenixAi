export interface WebSource { id: number; title: string; url: string; fetched: boolean; retrieved_at?: string; excerpt?: string }
export interface WebMeta {
  mode: string; needed: boolean; reasons: string[]; sources: WebSource[]; note?: string;
  /** not_needed | unverified_disabled | unverified_offline | unverified_no_results | sources_found | grounded | grounded_weak | sources_not_cited */
  status: string;
  citations?: { valid_ids: number[]; invalid_ids: number[]; weak: { id: number; sentence: string }[]; has_citations: boolean };
}
export interface Metrics {
  tokens_per_sec?: number | null;
  completion_tokens?: number;
  first_token_ms?: number | null;
  wall_ms?: number;
  status?: string;
  web?: WebMeta;
  sources?: { path: string; start: number; end: number }[];
}
export interface Message { id?: number; role: "user" | "assistant" | "system"; content: string; model?: string | null; metrics?: Metrics | null; error?: string }
export interface Conversation { id: string; title: string; model: string; created_at: string; updated_at: string }
export interface Settings {
  ollama_url: string; model: string; system_prompt: string; num_ctx: number;
  temperature: number; num_predict: number; timeout_s: number; max_prompt_chars: number;
  workspace_num_ctx: number; workspace_context_chars: number;
  web_mode: "off" | "auto" | "always"; web_max_sources: number; web_timeout_s: number; web_contact: string;
  keep_alive: string; num_thread: number; memory_guard: boolean; min_free_mb: number;
}
export interface Health {
  backend: string; ollama: { connected: boolean; version?: string; error?: string };
  active_model: string; model_installed: boolean; memory: { total_mb: number | null; available_mb: number | null };
  loaded?: { name: string; size: number }[];
}
export interface ModelInfo { name: string; size: number; parameter_size?: string; quantization?: string }

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...init });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const b = await r.json();
      const d = b.detail;
      if (typeof d === "string") msg = d;
      else if (d && typeof d === "object" && !Array.isArray(d) && d.message) {
        msg = d.message + (Array.isArray(d.attempts) ? ` (after ${d.attempts.length} attempt${d.attempts.length > 1 ? "s" : ""})` : "");
      } else if (Array.isArray(d)) msg = d.map((x: { loc?: string[]; msg?: string }) => `${(x.loc ?? []).slice(1).join(".")}: ${x.msg}`).join("; ");
      else msg = JSON.stringify(d);
    } catch { /* ignore */ }
    throw new Error(msg);
  }
  return r.status === 204 ? (undefined as T) : r.json();
}

export const api = {
  health: () => j<Health>("/api/health"),
  models: () => j<{ active: string; models: ModelInfo[]; loaded: { name: string }[] }>("/api/models"),
  settings: () => j<Settings>("/api/settings"),
  saveSettings: (s: Partial<Settings>) => j<Settings>("/api/settings", { method: "PUT", body: JSON.stringify(s) }),
  conversations: () => j<Conversation[]>("/api/conversations"),
  createConversation: () => j<Conversation>("/api/conversations", { method: "POST", body: "{}" }),
  conversation: (id: string) => j<Conversation & { messages: Message[] }>(`/api/conversations/${id}`),
  deleteConversation: (id: string) => j<void>(`/api/conversations/${id}`, { method: "DELETE" }),
  unloadModel: () => j<{ unloaded: string }>("/api/models/unload", { method: "POST" }),
  webTest: () => j<{ ok: boolean; provider?: string; error?: string }>("/api/web/test", { method: "POST" }),
};

export type StreamEvent =
  | { type: "start"; model: string }
  | { type: "token"; content: string }
  | { type: "status"; message: string }
  | { type: "warning"; message: string }
  | { type: "sources"; sources: WebSource[] }
  | { type: "done"; message: Message }
  | { type: "error"; kind: string; message: string };

/** Streams NDJSON events. Abort the signal to stop generation (server saves the partial text). */
export function streamMessage(id: string, content: string, signal: AbortSignal, onEvent: (e: StreamEvent) => void) {
  return streamPost(`/api/conversations/${id}/messages`, { content }, signal, onEvent);
}

export async function streamPost<E = StreamEvent>(url: string, payload: unknown, signal: AbortSignal, onEvent: (e: E) => void) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), signal,
  });
  if (!r.ok || !r.body) {
    let msg = `${r.status} ${r.statusText}`;
    try { const b = await r.json(); if (typeof b.detail === "string") msg = b.detail; } catch { /* ignore */ }
    throw new Error(msg);
  }
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

// ---------------- PHOENIX Code
export interface Project { id: string; name: string; path: string }
export interface TreeEntry { name: string; path: string; type: "dir" | "file"; size: number | null }
export interface FileView { path: string; content: string; start: number; end: number; total_lines: number; truncated: boolean }
export interface SearchHit { path: string; line: number; text: string }
export interface DiffRow { kind: "equal" | "change" | "add" | "del"; left: { n: number; t: string } | null; right: { n: number; t: string } | null }
export interface PatchSummary { id: string; project_id: string; path: string; title: string; status: string; created_at: string; applied_at: string | null; rolled_back_at: string | null; note: string | null }
export interface PatchMeta { strategy: string; attempts: number; checks: { name: string; ok: boolean | null; detail?: string }[]; attempt_log?: { n: number; ok: boolean; error?: string }[] }
export interface Patch extends PatchSummary { rows: DiffRow[]; is_new_file: boolean; unified: string; meta?: PatchMeta | null }

const post = <T>(path: string, body?: unknown) => j<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const q = (o: Record<string, string | number | undefined>) =>
  Object.entries(o).filter(([, v]) => v !== undefined && v !== "").map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&");

export const codeApi = {
  projects: () => j<Project[]>("/api/projects"),
  addProject: (path: string) => post<Project>("/api/projects", { path }),
  pickFolder: () => post<{ path: string | null }>("/api/projects/pick-folder"),
  removeProject: (id: string) => j<void>(`/api/projects/${id}`, { method: "DELETE" }),
  tree: (id: string, path = "") => j<TreeEntry[]>(`/api/projects/${id}/tree?${q({ path })}`),
  file: (id: string, path: string) => j<FileView>(`/api/projects/${id}/file?${q({ path })}`),
  search: (id: string, text: string, glob?: string) => j<SearchHit[]>(`/api/projects/${id}/search?${q({ q: text, glob })}`),
  files: (id: string, glob: string) => j<{ path: string; size: number }[]>(`/api/projects/${id}/files?${q({ glob })}`),
  generatePatch: (id: string, body: { path: string; instruction: string; new_file?: boolean; start?: number; end?: number }) =>
    post<Patch>(`/api/projects/${id}/patches/generate`, body),
  patches: (id: string) => j<PatchSummary[]>(`/api/projects/${id}/patches`),
  patch: (pid: string) => j<Patch>(`/api/patches/${pid}`),
  apply: (pid: string) => post<Patch>(`/api/patches/${pid}/apply`),
  reject: (pid: string) => post<Patch>(`/api/patches/${pid}/reject`),
  rollback: (pid: string) => post<Patch>(`/api/patches/${pid}/rollback`),
};

export interface BenchResult { id: string; category: string; passed: boolean; detail: string; tool_ok: boolean | null; latency_ms?: number; tokens_per_sec?: number | null; attempts?: number }
export interface BenchSummary {
  model: string; finished_at: string; tool_strategy?: string | null; categories: Record<string, { passed: number; total: number }>;
  tool_format_success: { ok: number; total: number }; avg_tokens_per_sec: number | null; avg_latency_s: number | null;
  max_latency_s: number | null; min_available_ram_mb: number | null; model_ram_mb: number | null; results: BenchResult[];
}
export type BenchEvent =
  | { type: "start"; model: string; total: number } | { type: "task_start"; id: string; category: string }
  | { type: "task_done"; result: BenchResult } | { type: "summary"; summary: BenchSummary } | { type: "error"; kind: string; message: string };
export const benchApi = {
  history: () => j<{ id: number; model: string; created_at: string; results: BenchSummary }[]>("/api/benchmark/history"),
};
