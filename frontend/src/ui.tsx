import { useCallback, useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { IconAlert, IconClose } from "./Icons";

type IconBtnProps = ButtonHTMLAttributes<HTMLButtonElement> & { label: string };

/** Square icon-only button. `label` becomes both the accessible name and the tooltip. */
export function IconButton({ label, className = "", children, ...rest }: IconBtnProps) {
  return (
    <button aria-label={label} title={label} {...rest}
      className={`press inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-zinc-400 transition hover:bg-zinc-800 hover:text-zinc-100 disabled:opacity-40 ${className}`}>
      {children}
    </button>
  );
}

const tones = {
  info: "border-zinc-700 bg-zinc-900 text-zinc-300",
  warn: "border-amber-700/60 bg-amber-950/40 text-amber-200",
  error: "border-red-800/60 bg-red-950/40 text-red-300",
} as const;

/** Inline notice with an optional dismiss button. */
export function Banner({ tone = "info", children, onDismiss, role }: { tone?: keyof typeof tones; children: ReactNode; onDismiss?: () => void; role?: "alert" | "status" }) {
  return (
    <div role={role ?? (tone === "error" ? "alert" : "status")} className={`fade-up flex items-start gap-2 rounded-xl border px-3 py-2 text-sm ${tones[tone]}`}>
      {tone !== "info" && <IconAlert size={16} className="mt-0.5 shrink-0" />}
      <div className="min-w-0 flex-1">{children}</div>
      {onDismiss && <button onClick={onDismiss} aria-label="Dismiss" className="shrink-0 opacity-70 hover:opacity-100"><IconClose size={14} /></button>}
    </div>
  );
}

/** Modal sheet: right-hand panel on desktop, full screen on phones. Escape or backdrop closes; focus is trapped. */
export function Sheet({ title, children, onClose, footer }: { title: string; children: ReactNode; onClose: () => void; footer?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [closing, setClosing] = useState(false);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const closingRef = useRef(false);
  const requestClose = useCallback(() => { if (closingRef.current) return; closingRef.current = true; setClosing(true); setTimeout(() => onCloseRef.current(), 180); }, []);
  const closeRef = useRef(requestClose); // parents pass inline handlers; the effect below must not re-run (and steal focus) on every render
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,select,textarea,button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { closeRef.current(); return; }
      if (e.key !== "Tab" || !ref.current) return;
      const f = [...ref.current.querySelectorAll<HTMLElement>("input,select,textarea,button,[href]")].filter((el) => !el.hasAttribute("disabled"));
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus(); };
  }, []);
  return (
    <div className={`${closing ? "backdrop-out" : "backdrop-in"} fixed inset-0 z-40 flex justify-end bg-black/50 backdrop-blur-sm`} onClick={requestClose}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className={`${closing ? "sheet-out" : "sheet-in"} flex h-full w-full flex-col border-l border-zinc-800 bg-zinc-950 shadow-2xl sm:max-w-xl`}>
        <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
          <h2 className="text-base font-semibold text-zinc-50">{title}</h2>
          <IconButton label="Close" onClick={requestClose}><IconClose /></IconButton>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-6">{children}</div>
        {footer && <div className="flex justify-end gap-2 border-t border-zinc-800 bg-zinc-950 px-5 py-3">{footer}</div>}
      </div>
    </div>
  );
}

export const btnPrimary = "ember-bg press inline-flex items-center justify-center gap-1.5 rounded-lg px-3.5 py-1.5 text-sm font-medium text-white shadow-sm transition hover:brightness-110 disabled:opacity-40 disabled:hover:brightness-100";
export const btnGhost = "press inline-flex items-center justify-center gap-1.5 rounded-lg border border-zinc-700 px-3 py-1.5 text-sm text-zinc-200 transition hover:bg-zinc-800 disabled:opacity-40";
export const inputCls = "w-full rounded-lg border border-zinc-700 bg-zinc-900 px-3 py-1.5 text-sm text-zinc-100 placeholder:text-zinc-600 transition focus:border-orange-500";
