import { useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { IconCheck, IconCopy } from "./Icons";

function textOf(node: ReactNode): string {
  if (typeof node === "string") return node;
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (node && typeof node === "object" && "props" in node) return textOf((node as { props: { children?: ReactNode } }).props.children);
  return "";
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const [copied, setCopied] = useState(false);
  const code = textOf(children).replace(/\n$/, "");
  const lang = /language-(\w+)/.exec(((children as { props?: { className?: string } })?.props?.className) ?? "")?.[1] ?? "code";
  const copy = async () => {
    try { await navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* clipboard unavailable */ }
  };
  return (
    <div className="my-3 overflow-hidden rounded-xl border border-zinc-700 bg-zinc-900">
      <div className="flex items-center justify-between border-b border-zinc-700 bg-zinc-800 px-3 py-1.5 text-xs text-zinc-400">
        <span className="font-mono">{lang}</span>
        <button onClick={copy} className="flex items-center gap-1 rounded-md px-2 py-0.5 hover:bg-zinc-700 hover:text-zinc-50">{copied ? <IconCheck size={13} /> : <IconCopy size={13} />}{copied ? "Copied!" : "Copy"}</button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export default function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[rehypeHighlight, { detect: true }]]} components={{ table: ({ children }) => <div className="md-table"><table>{children}</table></div>, pre: ({ children }) => <CodeBlock>{children}</CodeBlock>, a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-sky-400 underline-offset-2 hover:underline">{children}</a> }}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
