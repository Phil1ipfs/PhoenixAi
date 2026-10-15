import type { WebMeta, WebSource } from "./api";
import { IconAlert, IconGlobe } from "./Icons";

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

/** Page titles scraped from menus can arrive as "Dubai | AccuWeatherChevron downChevron down…": keep the readable head. */
export function cleanTitle(title: string | undefined, url: string): string {
  const t = (title ?? "").replace(/(chevron ?(down|up|left|right))+/gi, " ").split(/\s[|–—]\s/)[0].replace(/\s+/g, " ").trim();
  if (!t) return host(url);
  return t.length > 90 ? t.slice(0, 88).trimEnd() + "…" : t;
}

/** Turn [n] markers into small source chips after the text (domain name, full title on hover), but ONLY for ids that
 *  exist in the retrieved source list. Markdown form: [domain](<url> "cite|title"); Markdown.tsx renders it as a chip. */
export function linkCitations(text: string, sources: WebSource[] | undefined): string {
  if (!sources?.length) return text;
  const byId = new Map(sources.map((s) => [s.id, s]));
  const chip = (s: WebSource) => ` [${host(s.url).replace(/[[\]\\]/g, "")}](<${s.url}> "cite|${cleanTitle(s.title, s.url).replace(/["\\]/g, "'")}")`;
  return text.split("\n").map((line) => {
    // "1. [1] Some fact" would show the number twice: move leading markers to the end of the line, after the text.
    const lead = /^(\s*(?:\d+[.)]|[-*•])\s+)((?:\[\d{1,3}\]\s*)+)(.*)$/.exec(line);
    if (lead) line = `${lead[1]}${lead[3]} ${lead[2].trim()}`;
    let prev = "";
    return line.replace(/\s?\[(\d{1,3})\]/g, (m, n) => {
      const s = byId.get(Number(n));
      if (!s) return m;
      const c = chip(s);
      if (c === prev) return "";   // same source cited twice in a row: show one chip
      prev = c;
      return c;
    });
  }).join("\n");
}

/** What the search actually did (from the backend's request log, not from the model): live, cached, or failed. */
function SearchLine({ web, onFresh }: { web: WebMeta; onFresh?: () => void }) {
  const s = web.search;
  if (!s) return null;
  if (s.cached) {
    return (
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-zinc-400" data-testid="search-line">
        <IconGlobe size={13} className="shrink-0" />
        <span>Cached results used: retrieved at {s.cached_at?.slice(11, 16)}, not re-checked for this answer.</span>
        {onFresh && <button onClick={onFresh} className="rounded-md border border-zinc-700 px-2 py-0.5 text-zinc-300 hover:bg-zinc-800">Search again</button>}
      </div>
    );
  }
  const ok = (s.log ?? []).filter((e) => e.step === "search" && e.status === "ok").map((e) => e.provider);
  if (!ok.length) return null;                     // failures are explained by the banner below
  return <div className="mt-2 flex items-center gap-1.5 text-xs text-zinc-400" data-testid="search-line"><IconGlobe size={13} className="shrink-0" />Live search completed · {ok.join(", ")} · {((s.duration_ms ?? 0) / 1000).toFixed(1)} s</div>;
}

/** Every request PHOENIX made for this answer: provider, query, status, timing. */
export function SearchDetails({ web }: { web?: WebMeta }) {
  const log = web?.search?.log;
  if (!log?.length) return null;
  return (
    <details className="mt-2 text-xs text-zinc-500" data-testid="search-details">
      <summary className="cursor-pointer select-none hover:text-zinc-300">Search details ({log.length} step{log.length === 1 ? "" : "s"})</summary>
      <div className="mt-1 space-y-0.5 rounded-lg border border-zinc-800 p-2 font-mono text-[11px]">
        <div>query: {web?.search?.query}</div>
        {log.map((e, i) => (
          <div key={i} className="break-all">
            {e.requested_at?.slice(11, 19)} · {e.step} · {e.provider ?? ""} · {e.status}{e.error ? ` (${e.error})` : ""}{e.duration_ms != null ? ` · ${e.duration_ms} ms` : ""}
            {e.url ? ` · ${host(e.url)}` : ""}{e.urls ? ` · ${e.urls.length} result(s)` : ""}{e.published ? ` · published ${e.published}` : ""}
            {e.used_in_answer != null ? ` · ${e.used_in_answer ? "cited" : "not cited"}` : ""}
          </div>
        ))}
      </div>
    </details>
  );
}

/** Deterministic, model-independent verification banner / chip. */
export function WebStatus({ web, onFresh }: { web?: WebMeta; onFresh?: () => void }) {
  if (!web) return null;
  return <><SearchLine web={web} onFresh={onFresh} /><StatusBanner web={web} /></>;
}

function StatusBanner({ web }: { web: WebMeta }) {
  const n = web.sources?.length ?? 0;
  const why = web.note ? ` (${web.note})` : "";
  const warn = (text: string) => <div role="status" className="mt-2 flex items-start gap-2 rounded-xl border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-xs text-amber-200"><IconAlert size={14} className="mt-px shrink-0" /><span>{text}</span></div>;
  const verdict = web.verification?.verdict === "supported" ? "Answer supported by the sources (automatic check)." : "Answer not independently verified.";
  switch (web.status) {
    case "unverified_disabled":
      return warn("This looks time-sensitive, but internet access is off — the answer comes from the model’s memory and may be outdated. Turn on Web (Auto) to check live sources.");
    case "unverified_offline":
      return warn(`Live search unavailable${why}. This answer comes from the local model’s built-in knowledge (offline fallback) and may be outdated.`);
    case "unverified_no_results":
      return warn(`No relevant sources found${why}, so this isn’t verified. The answer comes from the local model’s built-in knowledge.`);
    case "search_skipped":
      return <div role="status" className="mt-2 flex items-start gap-1.5 text-xs text-zinc-400"><IconGlobe size={13} className="mt-px shrink-0" /><span>Live search skipped: {web.note}. This answer comes from the local model only.</span></div>;
    case "sources_not_cited":
      return warn("Live sources were found, but the answer doesn’t cite them — treat it as unverified.");
    case "grounded_weak":
      return warn(`Some statements couldn’t be matched to the cited source text${web.citations?.invalid_ids.length ? " and an invalid citation was removed" : ""}. Check the sources.`);
    case "weather_live":
      return <div className="mt-2 flex items-start gap-1.5 text-xs text-emerald-400"><IconGlobe size={13} className="mt-px shrink-0" /><span>Live data, fetched just now and validated. {web.note}</span></div>;
    case "weather_unavailable":
      return warn("Live weather data could not be retrieved or failed validation, so no temperature is shown. Nothing was estimated.");
    case "sources_conflict":
      return warn("The retrieved sources report different values, so no agreement is claimed. They may describe different places or times. Check the sources.");
    case "grounded":
      return <div className="mt-2 flex items-center gap-1.5 text-xs text-emerald-400"><IconGlobe size={13} className="shrink-0" /> Cited from {n} live source{n === 1 ? "" : "s"}. {verdict} Citations match the retrieved text; that doesn’t guarantee the answer is right.</div>;
    default:
      return null;
  }
}

export function SourceList({ web }: { web?: WebMeta }) {
  if (!web?.sources?.length) return null;
  return (
    <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-900 p-3 text-xs" data-testid="sources">
      <div className="mb-1 font-medium text-zinc-400">Sources</div>
      <ol className="space-y-1">
        {web.sources.map((s) => (
          <li key={s.id} className="flex gap-2">
            <span className="w-5 shrink-0 text-zinc-500">{s.id}.</span>
            <span className="min-w-0">
              <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.title} className="text-sky-400 hover:underline">{cleanTitle(s.title, s.url)}</a>
              <span className="ml-2 text-zinc-500">{host(s.url)} · {s.fetched ? "page read" : "search snippet only"}{s.published ? ` · published ${s.published}` : ""}{s.retrieved_at ? ` · ${s.cached ? "cached, retrieved" : "retrieved"} ${s.retrieved_at.slice(11, 16)}` : ""}{s.used === false ? " · not cited" : ""}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
