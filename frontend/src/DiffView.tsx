import type { DiffRow } from "./api";

const bg: Record<string, string> = { equal: "", change: "bg-amber-950/40", add: "bg-emerald-950/50", del: "bg-red-950/50" };

/** Side-by-side diff. Unchanged runs longer than 8 lines are collapsed to keep large files readable. */
export default function DiffView({ rows }: { rows: DiffRow[] }) {
  const out: React.ReactNode[] = [];
  for (let i = 0; i < rows.length; ) {
    if (rows[i].kind === "equal") {
      let j = i;
      while (j < rows.length && rows[j].kind === "equal") j++;
      const run = rows.slice(i, j);
      if (run.length > 8) {
        run.slice(0, 3).forEach((r, k) => out.push(<Row key={`${i}a${k}`} r={r} />));
        out.push(<tr key={`${i}c`}><td colSpan={4} className="bg-zinc-900 text-center text-xs text-zinc-500">… {run.length - 6} unchanged lines …</td></tr>);
        run.slice(-3).forEach((r, k) => out.push(<Row key={`${i}b${k}`} r={r} />));
      } else run.forEach((r, k) => out.push(<Row key={`${i}d${k}`} r={r} />));
      i = j;
    } else { out.push(<Row key={i} r={rows[i]} />); i++; }
  }
  return (
    <div className="overflow-auto rounded-xl border border-zinc-700 text-xs" data-testid="diff">
      <table className="w-full table-fixed border-collapse font-mono">
        <colgroup><col className="w-10" /><col /><col className="w-10" /><col /></colgroup>
        <tbody>{out}</tbody>
      </table>
    </div>
  );
}

function Row({ r }: { r: DiffRow }) {
  const l = r.left, rt = r.right;
  return (
    <tr className={bg[r.kind]} data-kind={r.kind}>
      <td className="select-none px-1 text-right text-zinc-600">{l?.n}</td>
      <td className={`whitespace-pre-wrap break-all px-1 ${r.kind === "del" || r.kind === "change" ? "text-red-300" : ""}`}>{l?.t}</td>
      <td className="select-none border-l border-zinc-800 px-1 text-right text-zinc-600">{rt?.n}</td>
      <td className={`whitespace-pre-wrap break-all px-1 ${r.kind === "add" || r.kind === "change" ? "text-emerald-300" : ""}`}>{rt?.t}</td>
    </tr>
  );
}
