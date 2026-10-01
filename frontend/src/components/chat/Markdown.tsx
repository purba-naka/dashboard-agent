import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./chat.module.css";

// HTML mentah dari model tidak dirender (default react-markdown), jadi aman dari XSS.
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  table: ({ children }) => (
    <div className={styles.tableWrap}>
      <table>{children}</table>
    </div>
  ),
};

/**
 * Render teks agent (Markdown + GFM: tabel, list, code, link).
 * `memo`: parse Markdown mahal; tanpa ini setiap ketikan di input chat
 * mem-parse ulang seluruh riwayat.
 */
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
