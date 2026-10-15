import type { WebMeta, WebSource } from "./api";
import { IconAlert, IconGlobe } from "./Icons";

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

/** Turn [n] markers into links, but ONLY for ids that exist in the retrieved source list. */
export function linkCitations(text: string, sources: WebSource[] | undefined): string {
  if (!sources?.length) return text;
  const byId = new Map(sources.map((s) => [s.id, s]));
  return text.replace(/\[(\d{1,3})\]/g, (m, n) => {
    const s = byId.get(Number(n));
    return s ? `[\\[${n}\\]](${s.url})` : m;
  });
}

/** Deterministic, model-independent verification banner / chip. */
export function WebStatus({ web }: { web?: WebMeta }) {
  if (!web) return null;
  const n = web.sources?.length ?? 0;
  const why = web.note ? ` (${web.note})` : "";
  const warn = (text: string) => <div role="status" className="mt-2 flex items-start gap-2 rounded-xl border border-amber-700/60 bg-amber-950/40 px-3 py-2 text-xs text-amber-200"><IconAlert size={14} className="mt-px shrink-0" /><span>{text}</span></div>;
  switch (web.status) {
    case "unverified_disabled":
      return warn("This looks time-sensitive, but internet access is off — the answer comes from the model’s memory and may be outdated. Turn on Web (Auto) to check live sources.");
    case "unverified_offline":
      return warn(`Couldn’t verify live${why}. The answer comes from the model’s memory and may be outdated.`);
    case "unverified_no_results":
      return warn("The web search returned nothing usable, so this isn’t verified. The answer comes from the model’s memory.");
    case "sources_not_cited":
      return warn("Live sources were found, but the answer doesn’t cite them — treat it as unverified.");
    case "grounded_weak":
      return warn(`Some statements couldn’t be matched to the cited source text${web.citations?.invalid_ids.length ? " and an invalid citation was removed" : ""}. Check the sources.`);
    case "grounded":
      return <div className="mt-2 flex items-center gap-1.5 text-xs text-emerald-400"><IconGlobe size={13} className="shrink-0" /> Cited from {n} live source{n === 1 ? "" : "s"}. Citations match the retrieved text; that doesn’t guarantee the answer is right.</div>;
    default:
      return null;
  }
}

export function SourceList({ web }: { web?: WebMeta }) {
  if (!web?.sources?.length) return null;
  return (
    <div className="mt-3 rounded-xl border border-zinc-800 bg-zinc-900 p-3 text-xs" data-testid="sources">
      <div className="mb-1 font-medium text-zinc-400">Live sources</div>
      <ol className="space-y-1">
        {web.sources.map((s) => (
          <li key={s.id} className="flex gap-2">
            <span className="w-5 shrink-0 text-zinc-500">[{s.id}]</span>
            <span className="min-w-0">
              <a href={s.url} target="_blank" rel="noopener noreferrer" className="text-sky-400 hover:underline">{s.title || host(s.url)}</a>
              <span className="ml-2 text-zinc-500">{host(s.url)} · {s.fetched ? "page read" : "search snippet only"}{s.retrieved_at ? ` · ${s.retrieved_at.slice(11, 16)}` : ""}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
