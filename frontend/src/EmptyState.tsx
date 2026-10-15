import type { Health } from "./api";
import { Logo } from "./Icons";

const SUGGESTIONS = [
  { title: "Explain code", text: "Explain how quicksort works, with a short Python example." },
  { title: "Debug an error", text: "Why would I get “TypeError: 'NoneType' object is not subscriptable” in Python, and how do I fix it?" },
  { title: "Check the web", text: "Who is the current prime minister of the UK? Verify against live sources." },
  { title: "Write a function", text: "Write a TypeScript function that debounces another function, with types." },
];

export default function EmptyState({ health, onPick }: { health: Health | null; onPick: (text: string) => void }) {
  return (
    <div className="fade-up mx-auto mt-[10vh] flex max-w-2xl flex-col items-center px-2 text-center">
      <Logo size={56} live />
      <h1 className="mt-4 text-2xl font-semibold tracking-tight text-zinc-50">What are we building?</h1>
      <p className="mt-2 max-w-md text-sm text-zinc-400">Runs locally on your machine. Turn on <b className="text-zinc-200">Web: auto</b> to verify time-sensitive questions against live sources.</p>
      {health && !health.ollama.connected && <p role="alert" className="mt-3 text-sm text-red-400">Ollama isn’t reachable. Start it, then reload.</p>}
      <div className="stagger mt-8 grid w-full gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((s) => (
          <button key={s.title} onClick={() => onPick(s.text)} className="lift press rounded-2xl border border-zinc-800 bg-zinc-900/80 p-3.5 text-left transition hover:border-orange-500/50 hover:bg-zinc-800/70">
            <div className="text-sm font-medium text-zinc-100">{s.title}</div>
            <div className="mt-0.5 line-clamp-2 text-xs text-zinc-500">{s.text}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
