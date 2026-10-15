// log_watch: placement, quoting, idempotency, sha conflicts, caps and UTF-8 (LOGBOOK-SPEC §2.2, §8).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readLog, parseEntries, toBase64, fromBase64, insertEntry, setNow, FILE_MAX_BYTES } from "../src/logbook.ts";
import { ALPHA, client, fakeGithub, standardRepo, dashboard } from "./fakes.ts";

const at = ALPHA.indexOf("## Log\n") + "## Log\n".length;
const alpha = (fake: ReturnType<typeof standardRepo>) => fake.repo.get("projects/alpha.md")!.text;
const puts = (fake: ReturnType<typeof standardRepo>) => fake.calls.filter((c) => c.method === "PUT");

test("inserts under ## Log after one blank line, quotes every line, leaves every other byte as it was", async () => {
  const fake = standardRepo();
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "Planned the beacon\n  Wrote the runbook  \n\n- Next: deploy" });
  assert.equal(isError, false, text);
  assert.match(text, /^Logged: 2026-10-14 — chatgpt on alpha/);
  const block = "### 2026-10-14 — chatgpt\n> Planned the beacon\n> Wrote the runbook\n>\n> - Next: deploy\n";
  assert.equal(alpha(fake), ALPHA.slice(0, at) + "\n" + block + "\n" + ALPHA.slice(at));
  const [put] = puts(fake);
  assert.equal(put.path, "projects/alpha.md");
  assert.equal(put.body!.sha, "sha2");
  assert.equal(put.body!.branch, "master");
  // The quoted "- Next:" line did not become a Now field, and the entry reads back as the newest.
  assert.equal(readLog(alpha(fake))[0].text, "Planned the beacon Wrote the runbook - Next: deploy");
});

test("caller tool id is written and duplicate detection is per tool", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  const codex = await c.call("log_watch", { project: "alpha", summary: "Same", tool: "codex" });
  assert.equal(codex.isError, false, codex.text);
  assert.match(codex.text, /^Logged: 2026-10-14 — codex on alpha/);
  assert.match(alpha(fake), /^### 2026-10-14 — codex/m);
  const duplicate = await c.call("log_watch", { project: "alpha", summary: "Same", tool: "codex" });
  assert.match(duplicate.text, /2026-10-14 — codex entry/);
  assert.equal(puts(fake).length, 1);
  const chatgpt = await c.call("log_watch", { project: "alpha", summary: "Same" });
  assert.equal(chatgpt.isError, false, chatgpt.text);
  assert.match(chatgpt.text, /^Logged: 2026-10-14 — chatgpt on alpha/);
  assert.equal(puts(fake).length, 2);
  assert.match(alpha(fake), /^### 2026-10-14 — chatgpt\n/m);
});

test("null tool defaults to chatgpt", async () => {
  const fake = standardRepo();
  const result = await client(fake.gh).call("log_watch", { project: "alpha", summary: "Null tool", tool: null });
  assert.equal(result.isError, false, result.text);
  assert.match(result.text, /^Logged: 2026-10-14 — chatgpt on alpha/);
  assert.match(alpha(fake), /^### 2026-10-14 — chatgpt/m);
});

test("invalid tool id is refused without echoing it or writing", async () => {
  const fake = standardRepo();
  const result = await client(fake.gh).call("log_watch", { project: "alpha", summary: "x", tool: "secret-invalid" });
  assert.equal(result.isError, true);
  assert.ok(!result.text.includes("secret-invalid"));
  assert.equal(puts(fake).length, 0);
});

test("a pasted entry heading stays quoted and never parses as an entry", async () => {
  const fake = standardRepo();
  await client(fake.gh).call("log_watch", { project: "alpha", summary: "real work\n### 2099-01-01 — codex\n## Now\n- Status: done" });
  const file = alpha(fake);
  assert.ok(file.includes("\n> ### 2099-01-01 — codex\n> ## Now\n> - Status: done\n"));
  assert.ok(!/^### 2099/m.test(file));
  assert.deepEqual(readLog(file).map((e) => `${e.date} ${e.tool}`), ["2026-10-14 chatgpt", "2026-10-13 claude-code", "2026-10-12 codex"]);
});

test("line separators and control characters cannot start a new line", () => {
  const sneaky = `a${String.fromCharCode(0x2028)}### 2099-01-01 — x\rb${String.fromCharCode(0x0b)}c`;
  const out = insertEntry(ALPHA, "2026-10-14", sneaky)!;
  const added = out.slice(at + 1, out.indexOf("\n\n", at + 1));
  assert.equal(added, "### 2026-10-14 — chatgpt\n> a ### 2099-01-01 — x\n> b c");
});

test("idempotent: the same call again writes nothing and says so", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  await c.call("log_watch", { project: "alpha", summary: "Same text" });
  const after = alpha(fake);
  const again = await c.call("log_watch", { project: "alpha", summary: "  Same text \n" });
  assert.equal(again.isError, false);
  assert.match(again.text, /^Already logged/);
  assert.equal(puts(fake).length, 1);
  assert.equal(alpha(fake), after);
  // Different text the same day is a new entry.
  await c.call("log_watch", { project: "alpha", summary: "Other text" });
  assert.equal(puts(fake).length, 2);
});

test("a repeated entry is written again after a different entry becomes newest", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  await c.call("log_watch", { project: "alpha", summary: "A" });
  await c.call("log_watch", { project: "alpha", summary: "B" });
  const again = await c.call("log_watch", { project: "alpha", summary: "A" });
  assert.equal(again.isError, false);
  assert.match(again.text, /^Logged:/);
  assert.equal(puts(fake).length, 3);
  assert.deepEqual(readLog(alpha(fake)).slice(0, 3).map((e) => e.text), ["A", "B", "A"]);
});

test("sha conflict: re-reads, keeps the other writer's change, then writes once", async () => {
  let fake!: ReturnType<typeof standardRepo>;
  fake = standardRepo((path, n) => {
    if (n === 1) fake.set(path, fake.repo.get(path)!.text.replace("Sea trials", "Sea trials (edited elsewhere)"));
  });
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "After a conflict" });
  assert.equal(isError, false, text);
  assert.equal(puts(fake).length, 2);
  const file = alpha(fake);
  assert.ok(file.includes("Sea trials (edited elsewhere)"));
  assert.equal(file.split("— chatgpt").length - 1, 1);
});

test("conflict where our first write actually landed: the retry sees it and skips", async () => {
  let fake!: ReturnType<typeof standardRepo>;
  fake = standardRepo((path, n) => {
    if (n === 1) {
      fake.set(path, insertEntry(fake.repo.get(path)!.text, "2026-10-14", "Landed anyway")!);
      return 412;
    }
  });
  const { text } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "Landed anyway" });
  assert.match(text, /^Already logged/);
  assert.equal(puts(fake).length, 1);
  assert.equal(alpha(fake).split("— chatgpt").length - 1, 1);
});

test("endless conflicts fail clearly after a few tries", async () => {
  const fake = standardRepo(() => 409);
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "never lands" });
  assert.equal(isError, true);
  assert.match(text, /kept changing.*Nothing was written/);
  assert.equal(puts(fake).length, 4);
  assert.equal(alpha(fake), ALPHA);
});

test("other GitHub write errors are tool errors, not retries", async () => {
  const fake = standardRepo(() => 403);
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "x" });
  assert.equal(isError, true);
  assert.match(text, /GitHub answered 403/);
  assert.equal(puts(fake).length, 1);
});

test("size cap, empty and non-string summaries are refused before any write", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  for (const summary of ["x".repeat(2001), "", "   \n\t", 42, undefined]) {
    const { isError } = await c.call("log_watch", { project: "alpha", summary });
    assert.equal(isError, true, JSON.stringify(summary)?.slice(0, 20));
  }
  assert.equal((await c.call("log_watch", { project: "alpha", summary: "y".repeat(2000) })).isError, false);
  assert.equal(puts(fake).length, 1);
});

test("date: defaults to UTC today, accepts ±1 day, refuses others and impossible dates", async () => {
  const fake = standardRepo();
  const c = client(fake.gh, new Date("2026-10-14T20:30:00Z")); // already 2026-10-15 in India
  assert.equal((await c.call("log_watch", { project: "alpha", summary: "a", date: "2026-10-15" })).isError, false);
  assert.equal((await c.call("log_watch", { project: "alpha", summary: "b", date: "2026-10-13" })).isError, false);
  for (const date of ["2026-10-16", "2026-10-12", "2099-01-01", "2026-02-30", "0000-01-01", "14-10-2026", 20261014]) {
    assert.equal((await c.call("log_watch", { project: "alpha", summary: "c", date })).isError, true, String(date));
  }
  await c.call("log_watch", { project: "alpha", summary: "d" });
  assert.deepEqual(readLog(alpha(fake)).slice(0, 3).map((e) => e.date), ["2026-10-15", "2026-10-14", "2026-10-13"]);
});

test("an explicit local date at UTC midnight boundary remains valid; year zero is not a calendar date", async () => {
  const fake = standardRepo();
  const c = client(fake.gh, new Date("2026-10-14T18:30:00Z")); // midnight in India, still Oct 14 UTC
  const local = await c.call("log_watch", { project: "alpha", summary: "local day", date: "2026-10-15" });
  assert.equal(local.isError, false, local.text);
  const utc = await c.call("log_watch", { project: "alpha", summary: "UTC day" });
  assert.equal(utc.isError, false, utc.text);
  assert.deepEqual(readLog(alpha(fake)).slice(0, 2).map((e) => e.date), ["2026-10-15", "2026-10-14"]);
  assert.deepEqual(parseEntries("### 0000-01-01 — codex\n- invalid\n### 0001-01-01 — codex\n- valid\n"), [
    { date: "0001-01-01", tool: "codex", text: "valid" },
  ]);
});

test("a file without ## Log is refused, nothing written", async () => {
  const fake = fakeGithub({ "dashboard.json": JSON.stringify(dashboard()), "projects/alpha.md": "# alpha\n- Status: active\n## Now\n- Next: x\n" });
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "x" });
  assert.equal(isError, true);
  assert.match(text, /no `## Log` line/);
  assert.equal(fake.calls.filter((c) => c.method === "PUT").length, 0);
});

test("UTF-8 round trip: em dashes, emoji and Devanagari survive, bytes match Node's encoder", async () => {
  const head = "﻿# alpha\n- Summary: नमस्ते — 🚢 voyage\n\n## log\n";
  const fake = fakeGithub({ "dashboard.json": JSON.stringify(dashboard()), "projects/alpha.md": head + "### 2026-10-01 — codex\n- ünïcödé\n" });
  await client(fake.gh).call("log_watch", { project: "alpha", summary: "Fixed the chart — नया 🚢" });
  const put = fake.calls.find((c) => c.method === "PUT")!;
  const expected = head + "\n### 2026-10-14 — chatgpt\n> Fixed the chart — नया 🚢\n\n### 2026-10-01 — codex\n- ünïcödé\n";
  assert.equal(put.body!.content, Buffer.from(expected, "utf8").toString("base64"));
  assert.equal(fromBase64(toBase64(expected)), expected);
});

test("CRLF files keep CRLF; a ## Log on the last line without a newline works", () => {
  const crlf = "# a\r\n## Log\r\n### 2026-10-01 — codex\r\n- old\r\n";
  assert.equal(insertEntry(crlf, "2026-10-14", "one\ntwo"), "# a\r\n## Log\r\n\r\n### 2026-10-14 — chatgpt\r\n> one\r\n> two\r\n\r\n### 2026-10-01 — codex\r\n- old\r\n");
  assert.equal(insertEntry("# a\n## Log", "2026-10-14", "x"), "# a\n## Log\n\n### 2026-10-14 — chatgpt\n> x\n");
});

test("only the first ## Log counts; a later one inside the log is just text", () => {
  const text = "# a\n## Log\n### 2026-10-01 — codex\n- old\n## Log\n";
  assert.ok(insertEntry(text, "2026-10-14", "x")!.startsWith("# a\n## Log\n\n### 2026-10-14 — chatgpt\n> x\n\n### 2026-10-01"));
});

test("blank lines under ## Log (one, several, whitespace-only) become exactly one; older entries keep every byte", () => {
  const old = "### 2026-10-01 — codex\n- old\n\n### 2026-09-01 — claude-code\n- older\n";
  const want = "# a\n## Log\n\n### 2026-10-14 — chatgpt\n> x\n\n" + old;
  for (const gap of ["", "\n", "\n\n\n", " \t\n  \n"]) assert.equal(insertEntry("# a\n## Log\n" + gap + old, "2026-10-14", "x"), want, JSON.stringify(gap));
  assert.equal(insertEntry("# a\r\n## Log\r\n\r\n \r\n### 2026-10-01 — codex\r\n", "2026-10-14", "x"),
    "# a\r\n## Log\r\n\r\n### 2026-10-14 — chatgpt\r\n> x\r\n\r\n### 2026-10-01 — codex\r\n");
});

test("inner blank lines stay as bare > lines; outer blank lines go", () => {
  const out = insertEntry("# a\n## Log\n", "2026-10-14", "\n\n  first\n\n\n  second  \n\n")!;
  assert.equal(out, "# a\n## Log\n\n### 2026-10-14 — chatgpt\n> first\n>\n>\n> second\n");
  assert.equal(readLog(out)[0].text, "first second");
});

test("newline style comes from the matched ## Log line: CR-only and CRLF", () => {
  const cr = "# a\r## Log\r### 2026-10-01 — codex\r- old\r";
  assert.equal(insertEntry(cr, "2026-10-14", "one\n\ntwo"), "# a\r## Log\r\r### 2026-10-14 — chatgpt\r> one\r>\r> two\r\r### 2026-10-01 — codex\r- old\r");
  assert.equal(insertEntry("# a\r\n## Log\r\n", "2026-10-14", "x\r\ny"), "# a\r\n## Log\r\n\r\n### 2026-10-14 — chatgpt\r\n> x\r\n> y\r\n");
  // ## Log ends the file: use the file's own newline style
  assert.equal(insertEntry("# a\r## Log", "2026-10-14", "x"), "# a\r## Log\r\r### 2026-10-14 — chatgpt\r> x\r");
});

test("file size cap: a write that would pass the byte cap is refused, nothing written", async () => {
  // 2-byte characters: the cap counts UTF-8 bytes, not characters
  const big = ALPHA + "- " + "é".repeat(Math.floor((FILE_MAX_BYTES - ALPHA.length - 100) / 2)) + "\n";
  assert.ok(new TextEncoder().encode(big).length <= FILE_MAX_BYTES);
  assert.ok(big.length < FILE_MAX_BYTES / 1.9);
  const fake = fakeGithub({ "dashboard.json": JSON.stringify(dashboard()), "projects/alpha.md": big });
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "y".repeat(200) });
  assert.equal(isError, true);
  assert.match(text, /over the 900000-byte cap\. Nothing was written/);
  assert.equal(fake.calls.filter((c) => c.method === "PUT").length, 0);
  assert.equal((await client(fake.gh).call("log_watch", { project: "alpha", summary: "ok" })).isError, false);
});

const NEW = "- Done: D\n- Next: N\n- Updated: 2026-10-14\n";

test("done/next: replaces the Now lines and sets Updated in the same commit as the entry", async () => {
  const fake = standardRepo();
  const { text, isError } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "Launched", done: "Launched the ship", next: "Open water" });
  assert.equal(isError, false, text);
  assert.match(text, /^Logged: 2026-10-14 — chatgpt on alpha, and set Done and Next in its Now section\./);
  const now = ALPHA.replace("- Done: Built the hull\n- Next: Sea trials\n", "- Done: Launched the ship\n- Next: Open water\n- Updated: 2026-10-14\n");
  assert.equal(alpha(fake), insertEntry(now, "2026-10-14", "Launched"));
  assert.equal(puts(fake).length, 1);
});

test("done/next: a file without ## Now gets one just above ## Log", async () => {
  const fake = standardRepo();
  await client(fake.gh).call("log_watch", { project: "beta", summary: "x", next: "Resume" });
  const beta = fake.repo.get("projects/beta.md")!.text;
  assert.ok(beta.startsWith("# beta\n- Status: paused\n\n## Now\n- Next: Resume\n- Updated: 2026-10-14\n\n## Log\n\n### 2026-10-14 — chatgpt\n> x\n"), beta.slice(0, 200));
});

test("done/next: one line each, # and > refused, cap 300, nothing written on refusal", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  const refusals: [Record<string, unknown>, RegExp][] = [
    [{ done: "# heading" }, /can't start with # or >/],
    [{ next: "  > quote" }, /can't start with # or >/],
    [{ done: " \n\t" }, /no printable text/],
    [{ next: "x".repeat(301) }, /cap is 300/],
    [{ done: 7 }, /must be text/],
  ];
  for (const [args, why] of refusals) {
    const r = await c.call("log_watch", { project: "alpha", summary: "x", ...args });
    assert.equal(r.isError, true, JSON.stringify(args));
    assert.match(r.text, why);
  }
  assert.equal(puts(fake).length, 0);
  await c.call("log_watch", { project: "alpha", summary: "y", done: "a\n## Log\n### 2099-01-01 — codex\u2028- Attention: x" });
  const file = alpha(fake);
  assert.ok(file.includes("\n- Done: a ## Log ### 2099-01-01 — codex - Attention: x\n"));
  assert.deepEqual(readLog(file).map((e) => e.date), ["2026-10-14", "2026-10-13", "2026-10-12"]);
});

test("done/next: the 300-character cap counts Unicode code points", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  const accepted = await c.call("log_watch", { project: "alpha", summary: "emoji", done: "😀".repeat(160) });
  assert.equal(accepted.isError, false, accepted.text);

  const refused = await c.call("log_watch", { project: "alpha", summary: "too long", next: "😀".repeat(301) });
  assert.equal(refused.isError, true);
  assert.match(refused.text, /301 characters/);
});

test("done/next: an entry that is already newest applies nothing, Now included", async () => {
  const fake = standardRepo();
  const c = client(fake.gh);
  await c.call("log_watch", { project: "alpha", summary: "Same", done: "First" });
  const after = alpha(fake);
  const again = await c.call("log_watch", { project: "alpha", summary: "Same", done: "Second", next: "Other" });
  assert.match(again.text, /^Already logged.*Nothing was written, so Done and Next were not changed either\.$/);
  assert.equal(alpha(fake), after);
  assert.equal(puts(fake).length, 1);
});

test("done/next: on a sha conflict the retry re-applies to the fresh file and keeps the other writer's edit", async () => {
  let fake!: ReturnType<typeof standardRepo>;
  fake = standardRepo((path, n) => {
    if (n === 1) fake.set(path, fake.repo.get(path)!.text.replace("Alpha — the first voyage", "Alpha (edited elsewhere)"));
  });
  const { isError, text } = await client(fake.gh).call("log_watch", { project: "alpha", summary: "x", next: "Open water" });
  assert.equal(isError, false, text);
  assert.equal(puts(fake).length, 2);
  const file = alpha(fake);
  assert.ok(file.includes("Alpha (edited elsewhere)"));
  assert.ok(file.includes("- Next: Open water\n- Updated: 2026-10-14\n"));
});

test("done/next: CRLF and CR files keep their newline style", () => {
  for (const eol of ["\r\n", "\r"]) {
    const before = "# a\n## Now\n- Done: d\n- Next: n\n\n## Log\n".replaceAll("\n", eol);
    assert.equal(setNow(before, "2026-10-14", { Done: "D", Next: "N" }), `# a\n## Now\n${NEW}\n## Log\n`.replaceAll("\n", eol));
  }
});

test("setNow mirrors the Python writer: the same shapes give the same text", () => {
  const cases: [string, string][] = [
    ["# a\n- Status: active\n\n## Log\n", `# a\n- Status: active\n\n## Now\n${NEW}\n## Log\n`],
    ["# a\n## now\n\n## Log\n", `# a\n## now\n${NEW}\n## Log\n`],
    ["# a\n## Now\n- Next: old\n## Notes\nkeep\n## Log\n", `# a\n## Now\n${NEW}## Notes\nkeep\n## Log\n`],
    ["# a\n## Now\n- Done:\n- Done: real\n- Next: n\n## Log\n", `# a\n## Now\n- Done:\n${NEW}## Log\n`],
    ["## Log\n### 2026-10-01 — codex\n- x\n", `## Now\n${NEW}\n## Log\n### 2026-10-01 — codex\n- x\n`],
    ["# a\n## Log\n## Now\n- Next: injected\n", `# a\n## Now\n${NEW}\n## Log\n## Now\n- Next: injected\n`],
  ];
  for (const [before, after] of cases) assert.equal(setNow(before, "2026-10-14", { Done: "D", Next: "N" }), after, before);
});

