import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// HTML mentah dari model tidak dirender (default react-markdown), jadi aman dari XSS.
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-md border">
      <table>{children}</table>
    </div>
  ),
};

const MARKDOWN = [
  "flex flex-col gap-2 leading-relaxed [overflow-wrap:anywhere]",
  "[&_:is(h1,h2,h3,h4)]:mt-1 [&_:is(h1,h2,h3,h4)]:text-[0.9375rem] [&_:is(h1,h2,h3,h4)]:font-semibold",
  "[&_:is(ul,ol)]:flex [&_:is(ul,ol)]:flex-col [&_:is(ul,ol)]:gap-1 [&_:is(ul,ol)]:pl-5 [&_ul]:list-disc [&_ol]:list-decimal",
  "[&_a]:font-medium [&_a]:underline [&_a]:underline-offset-2",
  "[&_:not(pre)>code]:rounded [&_:not(pre)>code]:bg-muted [&_:not(pre)>code]:px-1 [&_:not(pre)>code]:text-[0.85em]",
  "[&_pre]:overflow-x-auto [&_pre]:rounded-md [&_pre]:border [&_pre]:bg-muted [&_pre]:px-3 [&_pre]:py-2.5 [&_pre]:text-[0.8125rem]",
  "[&_blockquote]:border-l-2 [&_blockquote]:border-input [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground",
  "[&_table]:w-full [&_table]:text-[0.8125rem] [&_table]:tabular-nums",
  "[&_:is(th,td)]:border-b [&_:is(th,td)]:px-2.5 [&_:is(th,td)]:py-1.5 [&_:is(th,td)]:text-left [&_:is(th,td)]:whitespace-nowrap",
  "[&_th]:bg-muted [&_th]:font-semibold [&_tr:last-child_td]:border-b-0",
].join(" ");

/**
 * Render teks agent (Markdown + GFM: tabel, list, code, link).
 * `memo`: parse Markdown mahal; tanpa ini setiap ketikan di input chat
 * mem-parse ulang seluruh riwayat.
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className={MARKDOWN}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
