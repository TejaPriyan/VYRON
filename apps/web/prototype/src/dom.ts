/**
 * Small DOM helpers. They build elements from text and attributes only: there is no HTML-string path,
 * so a name or label from the API can never become markup. Inline `style` attributes are refused
 * because the page's Content-Security-Policy does not allow them; use classes, or CSSOM properties.
 */
const SVG_NS = "http://www.w3.org/2000/svg";

type AttrValue = string | number | boolean | null | undefined;
export type Attrs = Readonly<Record<string, AttrValue>>;
export type Child = Node | string | null | undefined | false;

function apply(element: Element, attrs: Attrs): void {
  for (const [name, value] of Object.entries(attrs)) {
    if (name === "style") throw new Error("inline style attributes are not allowed by the Content-Security-Policy");
    if (name.startsWith("on")) throw new Error("attach event listeners with addEventListener");
    if (value === null || value === undefined || value === false) continue;
    element.setAttribute(name, value === true ? "" : String(value));
  }
}

function append(element: Element, children: ReadonlyArray<Child>): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    element.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const element = document.createElement(tag);
  apply(element, attrs);
  append(element, children);
  return element;
}

export function s<K extends keyof SVGElementTagNameMap>(tag: K, attrs: Attrs = {}, ...children: Child[]): SVGElementTagNameMap[K] {
  const element = document.createElementNS(SVG_NS, tag);
  apply(element, attrs);
  append(element, children);
  return element;
}

/** Append children, skipping null, undefined and false (the DOM's own `append` rejects them). */
export function fill(element: Element, ...children: Child[]): void {
  append(element, children);
}

export function clear(element: Element): void {
  while (element.firstChild) element.removeChild(element.firstChild);
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}
