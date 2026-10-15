// The logbook, read and written through the GitHub contents API (LOGBOOK-SPEC §2, §5.1, §8).
// Nothing here knows about MCP; tools in mcp.ts call these and turn ToolError into isError results.

export type Gh = { repo: string; branch: string; token: string; fetch: (url: string, init?: RequestInit) => Promise<Response> };

export type Project = {
  name: string;
  file: string;
  status: string | null;
  idle: number | null;
  last_tool: string | null;
  unlogged: number | null;
  summary: string | null;
  done: string | null;
  next: string | null;
};
export type Dashboard = { generated: number | null; kpi: Record<string, number | null>; projects: Project[]; dataComplete: boolean | null };
export type Entry = { date: string; tool: string; text: string };

/** An expected failure whose message is safe to show the caller. */
export class ToolError extends Error {}

export const TOOL_ID = "chatgpt";
export const SUMMARY_MAX = 2000;
export const TEXT_MAX = 2000; // cap on any one text field returned by a read
export const FILE_MAX_BYTES = 900_000; // GitHub's contents API stops returning content above 1 MB
const MAX_ATTEMPTS = 4;

// ---- UTF-8 safe base64 (GitHub sends base64 of the raw bytes; em dashes must survive) ----

export function toBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export function fromBase64(b64: string): string {
  const bytes = Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0));
  // fatal: refuse to rewrite a file we can't decode; ignoreBOM: keep a BOM so the rest stays byte-identical
  return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
}

// ---- GitHub ----

function contents(g: Gh, path: string, init: RequestInit = {}) {
  const url = `https://api.github.com/repos/${g.repo}/contents/${path.split("/").map(encodeURIComponent).join("/")}`;
  return g.fetch(init.method === "PUT" ? url : `${url}?ref=${encodeURIComponent(g.branch)}`, {
    ...init,
    signal: AbortSignal.timeout(10_000),
    headers: {
      authorization: `Bearer ${g.token}`,
      accept: "application/vnd.github+json",
      "x-github-api-version": "2022-11-28",
      "user-agent": "waypoint-beacon",
      ...(init.body ? { "content-type": "application/json" } : {}),
    },
  });
}

async function readFile(g: Gh, path: string, label: string): Promise<{ text: string; sha: string }> {
  const res = await contents(g, path);
  if (!res.ok) throw new ToolError(`Could not read ${label}: GitHub answered ${res.status}.`);
  const j = (await res.json()) as { content?: string; encoding?: string; sha?: string };
  if (j.encoding !== "base64" || typeof j.content !== "string" || typeof j.sha !== "string") {
    throw new ToolError(`Could not read ${label}: unexpected GitHub response (file over 1 MB?).`);
  }
  try {
    return { text: fromBase64(j.content), sha: j.sha };
  } catch {
    throw new ToolError(`Could not read ${label}: it is not valid UTF-8.`);
  }
}

/** true = written, false = sha conflict (someone else wrote first). */
async function writeFile(g: Gh, path: string, text: string, sha: string, message: string, label: string) {
  const res = await contents(g, path, {
    method: "PUT",
    body: JSON.stringify({ message, content: toBase64(text), sha, branch: g.branch }),
  });
  if (res.ok) return true;
  if (res.status === 409 || res.status === 412) return false;
  throw new ToolError(`Could not write ${label}: GitHub answered ${res.status}.`);
}

export async function readDashboard(g: Gh): Promise<Dashboard> {
  const { text } = await readFile(g, "dashboard.json", "the chart (dashboard.json)");
  let raw: Record<string, unknown>;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new ToolError("The chart (dashboard.json) is not valid JSON.");
  }
  if (raw?.schema !== 1 || !Array.isArray(raw.projects)) throw new ToolError("The chart (dashboard.json) is not schema 1.");
  return cleanDashboard(raw);
}

// Values are checked, not just keys: a tampered chart could hide objects inside allowed fields (§8).
const str = (v: unknown, cap: number) => (typeof v === "string" ? v.slice(0, cap) : null);
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const rec = (v: unknown) => (v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const SOURCES = ["claude-code", "codex", "git", "logbook"];

function dataComplete(value: unknown): boolean | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const states = Object.values(value as Record<string, unknown>).map((source) => rec(source).state);
  if (states.some((state) => state === "degraded" || state === "unreadable")) return false;
  const sources = value as Record<string, unknown>;
  if (SOURCES.some((name) => rec(sources[name]).state === "absent")) return false;
  return SOURCES.every((name) => rec(sources[name]).state === "ok") ? true : null;
}

export const KPI_KEYS = ["tracked", "attention", "velocity", "stale", "untracked", "unlogged"];

/** Only the fields the beacon may use, each a capped string, a finite number or null. */
export function cleanDashboard(raw: Record<string, unknown>): Dashboard {
  const kpi = rec(raw.kpi);
  const projects: Project[] = [];
  for (const item of raw.projects as unknown[]) {
    const p = rec(item);
    const name = typeof p.name === "string" ? p.name : null;
    const file = typeof p.file === "string" ? p.file : null;
    if (!name || !file) continue; // unaddressable: drop
    projects.push({
      name,
      file,
      status: str(p.status, 40),
      idle: num(p.idle),
      last_tool: str(p.last_tool, 40),
      unlogged: num(p.unlogged),
      summary: str(p.summary, TEXT_MAX),
      done: str(p.done, TEXT_MAX),
      next: str(p.next, TEXT_MAX),
    });
  }
  return {
    generated: num(raw.generated),
    kpi: Object.fromEntries(KPI_KEYS.map((k) => [k, num(kpi[k])])),
    projects,
    dataComplete: dataComplete(raw.sources),
  };
}

/** The tracked project with exactly this name, or a ToolError naming the valid choices. */
export function findProject(d: Dashboard, name: unknown): Project {
  const names = d.projects.map((p) => p.name);
  const p = typeof name === "string" ? d.projects.find((x) => x.name === name) : undefined;
  if (!p) {
    const shown = typeof name === "string" ? `"${name.slice(0, 64)}"` : "(none)";
    throw new ToolError(`Unknown voyage (project) ${shown}. Tracked projects: ${names.join(", ")}`);
  }
  // Defence in depth: only ever touch projects/<name>.md, whatever the chart says.
  if (/[\/\\]|^[._]/.test(p.name) || p.file !== `projects/${p.name}.md`) {
    throw new ToolError(`The chart lists an unexpected file for "${p.name}"; refusing to touch it.`);
  }
  return p;
}

// ---- Project file grammar (mirrors waypoint/logbook.py) ----

const HEADING = /^### (\d{4}-\d{2}-\d{2}) [—-] (\S+)[ \t]*$/;
const LOG_LINE = /(^|\r\n|\r|\n)## Log[ \t]*(\r\n|\r|\n|$)/i;

export function isDate(s: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s) || s.startsWith("0000")) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/** Log entries in file order (§2.2). Headings with impossible dates are dropped with their body. */
export function parseEntries(body: string): Entry[] {
  const out: Entry[] = [];
  let cur: { date: string; tool: string; lines: string[] } | null = null;
  const flush = () => cur && out.push({ date: cur.date, tool: cur.tool, text: cur.lines.join(" ") });
  for (const raw of body.split(/\r\n|\r|\n/)) {
    const h = HEADING.exec(raw);
    if (h) {
      flush();
      cur = isDate(h[1]) ? { date: h[1], tool: h[2].toLowerCase(), lines: [] } : null;
      continue;
    }
    const line = raw.trim().replace(/^-(\s+|$)/, "").replace(/^>\s?/, "");
    if (cur && line) cur.lines.push(line);
  }
  flush();
  return out;
}

/** Where the log body starts: just after the first `## Log` line. */
function logStart(text: string) {
  const m = LOG_LINE.exec(text);
  return m ? { at: m.index + m[0].length, eol: m[2] } : null;
}

/** Newest first, same-date entries in file order, as readers sort them (§2.2). */
export function readLog(text: string): Entry[] {
  const s = logStart(text);
  return s ? parseEntries(text.slice(s.at)).sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0)) : [];
}

/** Every line quoted with "> " (§2.2), so pasted text can never become a heading or field. */
export function entryBlock(date: string, summary: string, eol: string) {
  const lines = summary
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u2028\u2029]/g, " ")
    .split("\n")
    .map((l) => l.trim());
  // Leading and trailing blank lines go; inner blank lines stay, as bare ">" lines.
  while (lines.length && !lines[0]) lines.shift();
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return [`### ${date} — ${TOOL_ID}`, ...lines.map((l) => (l ? `> ${l}` : ">"))].join(eol) + eol;
}

/**
 * Insert the entry below the first `## Log` line, after one blank line; blank lines between `## Log` and the
 * first entry are normalised to that one, and every other byte stays as it was.
 * Returns null when the newest entry (the first one, where writers put it) is already identical.
 */
export function insertEntry(text: string, date: string, summary: string): string | null {
  const s = logStart(text);
  if (!s) throw new ToolError("This project file has no `## Log` line, so there is nowhere to log. Nothing was written.");
  // The newline style of the matched `## Log` line (LF, CRLF or CR); if it ends the file, the file's first one.
  const eol = s.eol || (/\r\n|\r|\n/.exec(text)?.[0] ?? "\n");
  const block = entryBlock(date, summary, eol);
  const rest = text.slice(s.at);
  const [newest] = parseEntries(rest);
  const [mine] = parseEntries(block);
  if (newest && newest.date === mine.date && newest.tool === mine.tool && newest.text === mine.text) return null;
  // One blank line on each side of the new entry: "## Log", blank, entry, blank, older entries.
  const body = rest.replace(/^(?:[ \t]*(?:\r\n|\r|\n))+/, "");
  return text.slice(0, s.at) + (s.eol ? "" : eol) + eol + block + (body.trim() ? eol : "") + body;
}

/** Validate and clean log_watch input; returns the date to use. */
export function checkEntry(summary: unknown, date: unknown, now: Date): { summary: string; date: string } {
  if (typeof summary !== "string" || !summary.trim()) throw new ToolError("summary is required: 1–3 short lines on what was done.");
  if (summary.length > SUMMARY_MAX) throw new ToolError(`summary is ${summary.length} characters; the cap is ${SUMMARY_MAX}. Shorten it and link details instead.`);
  if (!/\n> /.test(entryBlock("0000-00-00", summary, "\n"))) throw new ToolError("summary has no printable text.");
  const today = now.toISOString().slice(0, 10);
  if (date === undefined || date === null || date === "") return { summary, date: today };
  if (typeof date !== "string" || !isDate(date)) throw new ToolError("date must be a real calendar date, YYYY-MM-DD.");
  const days = Math.abs(Date.parse(`${date}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000;
  if (days > 1) throw new ToolError(`date ${date} is more than a day from today (${today} UTC). Only today's work can be logged.`);
  return { summary, date };
}

export type LogResult = { written: boolean; date: string; attempts: number };

/**
 * Add one chatgpt entry to a tracked project: GET (text + sha), insert, PUT with that sha.
 * On a sha conflict, re-read, re-check idempotency, and try again, up to MAX_ATTEMPTS.
 */
export async function logWatch(g: Gh, d: Dashboard, name: unknown, summary: unknown, date: unknown, now: Date): Promise<LogResult> {
  const p = findProject(d, name);
  const e = checkEntry(summary, date, now);
  const label = `the log for ${p.name}`;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const { text, sha } = await readFile(g, p.file, label);
    const next = insertEntry(text, e.date, e.summary);
    if (next === null) return { written: false, date: e.date, attempts: attempt };
    const bytes = new TextEncoder().encode(next).length;
    if (bytes > FILE_MAX_BYTES) {
      throw new ToolError(`${label} would grow to ${bytes} bytes, over the ${FILE_MAX_BYTES}-byte cap. Nothing was written; trim or archive old entries first.`);
    }
    if (await writeFile(g, p.file, next, sha, `log: ${p.name} (${TOOL_ID}, via beacon)`, label)) {
      return { written: true, date: e.date, attempts: attempt };
    }
  }
  throw new ToolError(`${label} kept changing under us (${MAX_ATTEMPTS} conflicting writes). Nothing was written; try again shortly.`);
}

export async function readProjectLog(g: Gh, p: Project): Promise<Entry[]> {
  return readLog((await readFile(g, p.file, `the log for ${p.name}`)).text);
}
