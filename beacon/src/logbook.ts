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
  updated: string | null;
  now_behind: boolean | null;
};
export type Dashboard = { generated: number | null; kpi: Record<string, number | null>; projects: Project[]; dataComplete: boolean | null };
export type Entry = { date: string; tool: string; text: string };

/** An expected failure whose message is safe to show the caller. */
export class ToolError extends Error {}

export const TOOL_ID = "chatgpt";
export const KNOWN_TOOLS = ["claude-code", "codex", "chatgpt", "gemini-cli", "copilot-cli", "cursor", "windsurf", "kiro", "goose", "cline", "claude-desktop", "opencode", "amp"] as const;
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

export const KPI_KEYS = ["tracked", "attention", "velocity", "stale", "untracked", "unlogged", "behind"];

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
      updated: typeof p.updated === "string" && isDate(p.updated) ? p.updated : null,
      now_behind: typeof p.now_behind === "boolean" ? p.now_behind : null,
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
export function entryBlock(date: string, summary: string, eol: string, tool = TOOL_ID) {
  const lines = summary
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u2028\u2029]/g, " ")
    .split("\n")
    .map((l) => l.trim());
  // Leading and trailing blank lines go; inner blank lines stay, as bare ">" lines.
  while (lines.length && !lines[0]) lines.shift();
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return [`### ${date} — ${tool}`, ...lines.map((l) => (l ? `> ${l}` : ">"))].join(eol) + eol;
}

/**
 * Insert the entry below the first `## Log` line, after one blank line; blank lines between `## Log` and the
 * first entry are normalised to that one, and every other byte stays as it was.
 * Returns null when the newest entry (the first one, where writers put it) is already identical.
 */
export function insertEntry(text: string, date: string, summary: string, tool = TOOL_ID): string | null {
  const s = logStart(text);
  if (!s) throw new ToolError("This project file has no `## Log` line, so there is nowhere to log. Nothing was written.");
  // The newline style of the matched `## Log` line (LF, CRLF or CR); if it ends the file, the file's first one.
  const eol = s.eol || (/\r\n|\r|\n/.exec(text)?.[0] ?? "\n");
  const block = entryBlock(date, summary, eol, tool);
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

export const NOW_MAX = 300;
export type NowFields = { Done?: string; Next?: string };
const NOW_ORDER = ["Done", "Next", "Updated"] as const;

/** One `## Now` value for log_watch (§8.3): a single line of 1–300 characters that doesn't start with # or >. */
export function cleanNow(value: unknown, name: string): string {
  if (typeof value !== "string") throw new ToolError(`${name} must be text.`);
  const text = value.replace(/[\u0000-\u001f\u007f-\u009f]/g, " ").split(/\s+/).filter(Boolean).join(" ");
  if (!text) throw new ToolError(`${name} has no printable text.`);
  if ([...text].length > NOW_MAX) throw new ToolError(`${name} is ${[...text].length} characters; the cap is ${NOW_MAX}. Keep it to one short line.`);
  if (text[0] === "#" || text[0] === ">") throw new ToolError(`${name} can't start with # or >; write it as plain text.`);
  return text;
}

export function checkNow(done: unknown, next: unknown): NowFields {
  const out: NowFields = {};
  if (done !== undefined && done !== null) out.Done = cleanNow(done, "done");
  if (next !== undefined && next !== null) out.Next = cleanNow(next, "next");
  return out;
}

/** Set Done/Next in `## Now` and `- Updated: date` (§8.3); mirrors waypoint/watch.py set_now byte for byte. */
export function setNow(text: string, date: string, fields: NowFields): string {
  const m = LOG_LINE.exec(text);
  if (!m) throw new ToolError("This project file has no `## Log` line, so there is nowhere to log. Nothing was written.");
  const cut = m.index + m[1].length; // the head: everything before the `## Log` line, including its line break
  const eol = m[1] || m[2] || (/\r\n|\r|\n/.exec(text)?.[0] ?? "\n");
  const parts = text.slice(0, cut).split(/(\r\n|\r|\n)/);
  const lines: [string, string][] = [];
  for (let i = 0; i < parts.length; i += 2) lines.push([parts[i], parts[i + 1] ?? ""]);
  let now = lines.findIndex(([l]) => l.startsWith("## ") && l.slice(3).trim().toLowerCase() === "now");
  if (now < 0) {
    now = lines.length - 1; // the empty remainder after the head's last line break
    lines.splice(now, 0, ["## Now", eol], ["", eol]);
  }
  let end = lines.findIndex(([l], i) => i > now && l.startsWith("## "));
  if (end < 0) end = lines.length;
  const values: Record<string, string | undefined> = { ...fields, Updated: date };
  let after = now;
  for (const name of NOW_ORDER) {
    let hit = -1;
    for (let i = now + 1; i < end && hit < 0; i++) if (lines[i][0].startsWith(`- ${name}:`) && lines[i][0].length > name.length + 3) hit = i;
    for (let i = now + 1; i < end && hit < 0; i++) if (lines[i][0] === `- ${name}:`) hit = i;
    const value = values[name];
    if (value !== undefined) {
      const line = `- ${name}: ${value}`;
      if (hit < 0) {
        hit = after + 1;
        lines.splice(hit, 0, [line, eol]);
        end++;
      } else lines[hit][0] = line;
    }
    if (hit >= 0) after = Math.max(after, hit);
  }
  return lines.map(([l, s]) => l + s).join("") + text.slice(cut);
}

export type LogResult = { written: boolean; date: string; attempts: number; now: string[]; tool: string };

/**
 * Add one caller-attributed entry to a tracked project (and, with done/next, set Done, Next and Updated in ## Now):
 * GET (text + sha), insert, PUT with that sha. On a sha conflict, re-read, re-check idempotency, and try again,
 * up to MAX_ATTEMPTS. An entry that is already newest applies nothing, Now included.
 */
export async function logWatch(
  g: Gh, d: Dashboard, name: unknown, summary: unknown, date: unknown, now: Date, done?: unknown, next?: unknown, tool: unknown = TOOL_ID,
): Promise<LogResult> {
  tool ??= TOOL_ID;
  if (typeof tool !== "string" || !(KNOWN_TOOLS as readonly string[]).includes(tool)) throw new ToolError("tool must be one of the known tool ids.");
  const p = findProject(d, name);
  const e = checkEntry(summary, date, now);
  const fields = checkNow(done, next);
  const changed = Object.keys(fields);
  const label = `the log for ${p.name}`;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const { text, sha } = await readFile(g, p.file, label);
    const withEntry = insertEntry(text, e.date, e.summary, tool);
    if (withEntry === null) return { written: false, date: e.date, attempts: attempt, now: changed, tool };
    const updated = changed.length ? setNow(withEntry, e.date, fields) : withEntry;
    const bytes = new TextEncoder().encode(updated).length;
    if (bytes > FILE_MAX_BYTES) {
      throw new ToolError(`${label} would grow to ${bytes} bytes, over the ${FILE_MAX_BYTES}-byte cap. Nothing was written; trim or archive old entries first.`);
    }
    if (await writeFile(g, p.file, updated, sha, `log: ${p.name} (${tool}, via beacon)`, label)) {
      return { written: true, date: e.date, attempts: attempt, now: changed, tool };
    }
  }
  throw new ToolError(`${label} kept changing under us (${MAX_ATTEMPTS} conflicting writes). Nothing was written; try again shortly.`);
}

export async function readProjectLog(g: Gh, p: Project): Promise<Entry[]> {
  return readLog((await readFile(g, p.file, `the log for ${p.name}`)).text);
}
