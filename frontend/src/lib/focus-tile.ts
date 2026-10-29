/** Scroll ke tile dan beri sorotan sebentar. `false` bila tile tidak ada di DOM (mis. canvas OpenUI). */
export function focusTile(id: string, ms = 1500): boolean {
  const el = document.querySelector<HTMLElement>(`[data-tile-id="${CSS.escape(id)}"]`);
  if (!el) return false;
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
  el.setAttribute("data-highlight", "true");
  window.setTimeout(() => el.removeAttribute("data-highlight"), ms);
  return true;
}
