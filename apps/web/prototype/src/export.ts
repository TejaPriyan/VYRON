/**
 * Client-side JSON evidence export. Builds a downloadable file purely in memory from the data already
 * shown on the page; sends nothing over the network and stores nothing in the browser.
 */

export function safeFilename(stem: string): string {
  const clean = stem
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
  return `${clean || "vyron-export"}.json`;
}

export function downloadJson(stem: string, payload: unknown): void {
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json;charset=utf-8" });
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = safeFilename(stem);
  link.rel = "noopener";
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 1000);
}
