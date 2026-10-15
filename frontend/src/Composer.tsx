import { useEffect, useRef } from "react";
import type { Settings } from "./api";
import { IconGlobe, IconSend, IconStop } from "./Icons";

interface Props {
  value: string; busy: boolean; webMode?: Settings["web_mode"];
  onChange: (v: string) => void; onSend: () => void; onStop: () => void; onWebMode: (m: Settings["web_mode"]) => void;
}

/** Floating, auto-growing message box with the web-mode chip and send/stop button inside it. */
export default function Composer({ value, busy, webMode, onChange, onSend, onStop, onWebMode }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
  }, [value]);

  const can = value.trim().length > 0;
  return (
    <div className="mx-auto w-full max-w-3xl px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2">
      <div className={`composer glass rounded-3xl p-2 ${busy ? "glow-busy" : ""}`}>
        <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} rows={1} aria-label="Message"
          placeholder="Message PHOENIX…"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend(); } }}
          className="block max-h-[200px] w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 text-zinc-100 placeholder:text-zinc-500" />
        <div className="flex items-center gap-2 px-1 pt-1">
          {webMode && (
            <label className="composer-chip flex items-center gap-1.5 rounded-full border border-zinc-700 py-1 pl-2.5 pr-1 text-xs text-zinc-400 transition hover:border-zinc-600" title="Internet research">
              <IconGlobe size={14} className={webMode === "off" ? "" : "text-orange-500"} />
              <select aria-label="Web mode" value={webMode} onChange={(e) => onWebMode(e.target.value as Settings["web_mode"])} className="cursor-pointer bg-transparent pr-1 text-xs text-zinc-300 outline-none">
                <option value="off">Web: off</option><option value="auto">Web: auto</option><option value="always">Web: always</option>
              </select>
            </label>
          )}
          <span className="ml-auto hidden text-[11px] text-zinc-500 sm:inline">Enter to send · Shift+Enter for newline</span>
          {busy
            ? <button onClick={onStop} aria-label="Stop" title="Stop generating" className="press flex h-9 w-9 items-center justify-center rounded-full bg-zinc-100 text-zinc-900 transition hover:bg-white"><IconStop size={16} /></button>
            : <button onClick={onSend} disabled={!can} aria-label="Send" title="Send" className="ember-bg press flex h-9 w-9 items-center justify-center rounded-full text-white transition hover:brightness-110 disabled:opacity-30 disabled:hover:brightness-100"><IconSend size={18} /></button>}
        </div>
      </div>
    </div>
  );
}
