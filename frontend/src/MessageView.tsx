import { useState } from "react";
import type { Message } from "./api";
import { IconCheck, IconCopy, Logo } from "./Icons";
import Markdown from "./Markdown";
import { SearchDetails, SourceList, WebStatus, linkCitations } from "./Sources";

function Dots() {
  return <span className="inline-flex gap-1" aria-label="working">{[0, 150, 300].map((d) => <i key={d} style={{ animationDelay: `${d}ms` }} className="h-1.5 w-1.5 animate-pulse rounded-full bg-orange-500" />)}</span>;
}

export default function MessageView({ m, last, busy, statusMsg, onFresh }: { m: Message; last: boolean; busy: boolean; statusMsg: string; onFresh?: () => void }) {
  const [copied, setCopied] = useState(false);
  const web = m.metrics?.web;
  const streaming = busy && last;

  if (m.role === "user") {
    return (
      <div className="fade-up flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl rounded-br-lg bg-[var(--bubble)] px-4 py-2.5 leading-relaxed text-zinc-100">{m.content}</div>
      </div>
    );
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(m.content); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ }
  };
  const mt = m.metrics;
  return (
    <div className="fade-up group flex gap-3">
      <div className="mt-0.5 shrink-0"><Logo size={26} live={streaming} busy={streaming} /></div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 text-xs font-semibold tracking-wide text-zinc-400">PHOENIX</div>
        {m.content
          ? <div className={streaming ? "caret" : ""}><Markdown text={linkCitations(m.content, web?.sources)} /></div>
          : streaming ? <span className="flex items-center gap-2 text-sm text-zinc-400"><Dots />{statusMsg || "Thinking… (the first reply can take a while while the model loads)"}</span> : null}
        <SourceList web={web} />
        {!streaming && <WebStatus web={web} onFresh={last && !busy ? onFresh : undefined} />}
        {!streaming && <SearchDetails web={web} />}
        {!streaming && (m.content || mt?.status === "stopped") && (
          <div className="mt-2 flex items-center gap-3 text-xs text-zinc-500">
            {m.content && <button onClick={copy} aria-label="Copy reply" title="Copy reply" className="flex items-center gap-1 rounded-md px-1.5 py-1 opacity-100 transition hover:bg-zinc-800 hover:text-zinc-200 md:opacity-0 md:group-hover:opacity-100 md:focus-visible:opacity-100">
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}{copied ? "Copied" : "Copy"}
            </button>}
            {mt?.status === "stopped" && <span>stopped</span>}
          </div>
        )}
      </div>
    </div>
  );
}
