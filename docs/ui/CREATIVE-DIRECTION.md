> **Status: Phase 6 input (UI not built yet).** This is a cleaned copy of the original creative brief, with the export artifacts removed. Where the brief conflicts with the **resolutions below**, the resolutions win. They come from the Claude and Codex UI audits (2026-10-14). A working proposal exists in the maintainer's playground (not in this repo).
>
> **Resolved values and rules**
> - **Tokens:** canvas `#090A0F`; glass `rgba(22,24,34,.65)` with `backdrop-filter: blur(20px)`; specular border `rgba(255,255,255,.12)`.
> - **Colour:** saturated glows mean **status only**: attention coral/amber, active emerald, adrift cyan/blue, untracked violet/magenta. Navigators (Claude Code, Codex, ChatGPT) get **neutral badges with an icon and a text label**, never a status hue. Never use colour as the only signal.
> - **Data:** read from `dashboard.json` (LOGBOOK-SPEC §5.1).
>   - Stale means `idle > stale_days`, strictly.
>   - The pulse is a 5×7 grid covering 35 days.
>   - The segmented bar counts each project once, in this order: hazard > adrift > active > dormant.
> - **View-only:**
>   - Actions copy a command or some text to the clipboard. They never write files.
>   - Status and archive actions say "ask before changing Status".
>   - "Initialize" never writes a log entry.
> - **Untrusted text:** session titles are raw prompts, so treat all text as untrusted. Render it with `textContent`, never `innerHTML`. Use `Ctrl+K` on Windows.
> - **Deferred idea:** an MCP Apps view (`ui://waypoint/fix`) inside ChatGPT and Claude, not A2UI.
>
> **Historical body:** the brief below was written for the prototype, so it mentions `PROGRESS-CHECK`, `sync.py` and `DASHBOARD.md` as the underlying system. In Waypoint the UI reads **`dashboard.json`** (LOGBOOK-SPEC §5.1), produced by `waypoint fix`.
>
> **Reference images (Refs 1–4):** `ui-concept.jpg`, `widgets-ui-design.jpg`, `dashboard-inspiration.jpg`, `design-lab.jpg`.

# PROGRESS-CHECK: Creative Direction & UI/UX Specification

## 1. Core Design Philosophy

**PROGRESS-CHECK** is a unified, local-first command center that aggregates multi-agent development history (**Claude Code**, **Codex**, and **ChatGPT**) alongside project documentation (`projects/*.md`). 

Because the underlying system relies on `sync.py` parsing local session logs and markdown files into `DASHBOARD.md`, the visual interface must elevate raw markdown data into a **tactile, atmospheric, and high-signal telemetry board**. 

### Primary UX Directive: Actionable & Accessible Items First

The dashboard hierarchy is strictly governed by **immediate cognitive utility**. The end-user should never have to hunt for blocked tasks, unindexed work, or drifting repositories. High-priority KPIs, friction points (`Needs Attention`), and unindexed agent sessions (`Untracked Folders`) are surfaced at the highest visual elevation before historical logs or archived states.

---

## 2. Visual Inspiration & Synthesis (From Reference Boards)

### A. Luminous Bento Widgets & Inner-Glow Telemetry (Refs 1 & 2)

* **Visual Language:** Squircle and rounded-rectangle bento cards with deep, diffused internal radial gradients, soft gaussian blurs, and crisp 1px specular inner borders (`rgba(255, 255, 255, 0.14)`).

* **Typography & Micro-Visualizations:** High-contrast, oversized tabular numerals (`28/135`, `68%`, `179,498`) paired with clean sans-serif labels and monospace quotes/logs. Data is expressed through tactile micro-charts: pill-bar arrays, dot-matrix activity calendars, and radial progress arcs.

* **Application in PROGRESS-CHECK:** Used for the top-level **KPI Telemetry Row** (Needs Attention, Active Pulse, Going Stale, Untracked Folders) and agent activity heatmaps.

### B. Translucent Frosted Acrylic & Depth Stacking (Ref 3)

* **Visual Language:** Layered glassmorphic folder slabs with realistic edge refraction, frosted blur (`backdrop-filter: blur(24px)`), and 3D spatial stacking. Active or selected items shift forward in Z-space and illuminate with a vibrant semantic tint (e.g., emerald green), while inactive items recede into neutral monochrome glass.

* **Application in PROGRESS-CHECK:** Used for the **`projects/_archive/` Vault** and **Documentation Source-of-Truth Cards**, allowing dozens of historical or paused projects to sit compactly in a tactile "file rack" without cluttering the active workspace.

### C. Executive Hero Telemetry & Tabbed Folder Architecture (Ref 4)

* **Visual Language:** Ultra-dark obsidian canvas (`#0B0C10`) with a subtle atmospheric aurora bleed in the header. Features a commanding hero metric alongside a **segmented pill progress bar** (solid neon fills vs. hatched/striped pending states). Below, the workspace splits into a **Folder-Tab Main Stage** (left, 65% width) with a 3-column bento card grid, and a **Chronological Activity Feed** (right, 35% width) using interlocking, color-coded pastel/vivid timeline cards.

* **Application in PROGRESS-CHECK:** Forms the structural backbone of the entire viewport—bridging macro portfolio health, status-filtered project grids, and the live multi-agent session stream (`~/.claude`, `~/.codex`, manual ChatGPT logs).

---

## 3. Information Architecture & Viewport Layout

*Prioritizing accessible, high-urgency items at the top and left of the visual scanning path (F-Pattern / Z-Pattern).*

### Tier 1: Executive Header & Segmented Portfolio Bar (Top Most Accessible)

1. **Command Header:**

   * **Left:** Project identity (`PROGRESS-CHECK`), live `sync.py` status indicator, and a primary **"Run `sync.py`"** action trigger.

   * **Center/Hero:** Total Tracked Projects & Active Velocity KPI (massive light-weight numeral) alongside inline metadata pills (`Last Sync Timestamp`, `claude-code` hook status, `codex` hook status).

   * **Right:** Global `Cmd + K` search bar (filter by project, tool, or next step) and `+ New from _template.md` quick action.

2. **Segmented Portfolio Distribution Bar:**

   * Full-width horizontal pill bar inspired by Ref 4's Paid/Credits/Balance bar:

     * **Needs Attention (Coral/Amber Glow):** Solid high-contrast pill showing projects with open blockers.

     * **Active (Neon Emerald/Mint):** Solid vibrant pill showing projects with recent commits/sessions.

     * **Going Stale (Violet/Blue):** Translucent glowing pill showing active projects exceeding the idle-day threshold.

     * **Occasional / Paused (Hatched Vertical Stripes):** Textured dark pill representing dormant capacity.

### Tier 2: Action-First Bento KPI Row (4-Column Widget Grid)

Immediately below the segmented bar, four square/squircle bento widgets (Refs 1 & 2 style) surface the exact items requiring human intervention:

1. **Widget 01 — Needs Attention (Blocker Queue):**

   * *Visual:* Warm crimson-to-amber inner radial glow.

   * *Content:* Big counter of open issues across all projects + top 2 clickable blocker links that jump straight to the project's markdown file.

2. **Widget 02 — Untracked Folders (Zero-Slippage Radar):**

   * *Visual:* Electric violet-to-magenta inner glow.

   * *Content:* Directories where `~/.claude` or `~/.codex` worked recently that lack a `projects/<name>.md` file. Features a 1-click **"Initialize from `_template.md`"** button per folder.

3. **Widget 03 — Going Stale (Idle Drift Meter):**

   * *Visual:* Deep cyan-to-indigo inner glow with a vertical pill-bar meter (Ref 1 "daily workout" / Ref 2 "Heart rate" style).

   * *Content:* Highlights `active` projects whose idle days are creeping past threshold (e.g., `7d+ idle`), prompting a status downgrade or quick sprint.

4. **Widget 04 — Multi-Agent Pulse (30-Day Calendar Matrix):**

   * *Visual:* Emerald-to-lime inner glow with a 7x4 dot-matrix calendar grid (Ref 2 "Impressions" calendar style).

   * *Content:* Visualizes daily session density across Claude Code, Codex, and ChatGPT.

### Tier 3: Split Workspace (Main Project Grid + Live Agent Log Stream)

#### Left Column (65% Width): Folder-Tab Project Matrix

Styled with the sleek curved folder-tab header from Ref 4 (`Invoice lines` container):

* **Folder Tabs:** `All Active` (Default) | `Needs Attention` | `Going Stale` | `Occasional & Paused` | `Done & _archive/`

* **Project Cards (3-Column Grid):**

  Each card represents a single `projects/<slug>.md` file and displays:

  * **Header:** Project Title, Status Dot, and **Idle Days Badge** (`0d idle`, `4d idle`, `14d stale`).

  * **Summary:** 2-line crisp summary of what the project is.

  * **Next Step (High-Contrast Callout Box):** Monospace or highlighted pill showing the exact `Next:` item so the user knows what to prompt next.

  * **Attention Flag:** If an issue is open, a subtle amber/red banner sits pinned to the card.

  * **Footer:** Last tool badge (`Claude Code`, `Codex`, or `ChatGPT`) + direct external links to the **Repo Docs (Source of Truth)**.

#### Right Column (35% Width): Multi-Agent Activity Stream

Styled after Ref 4's right-hand `Activity / Upcoming` container:

* **Quick Tool Filters & Actions:** Top pill buttons for `All Tools`, `Claude Code`, `Codex`, `ChatGPT`, plus a **`+ Paste ChatGPT Summary`** button (solving the manual logging requirement for ChatGPT).

* **Interlocking Timeline Cards:**

  Chronological feed parsed from the dated logs across all project files:

  * **Claude Code Cards:** Soft Lavender / Violet tint (`#D8B4FE`) with terminal icon, timestamp, project tag, and session summary.

  * **Codex Cards:** Crisp Acid Lime / Yellow tint (`#FEF08A`) with code-agent icon, timestamp, project tag, and diff/task summary.

  * **ChatGPT Cards:** Warm Peach / Coral tint (`#FDBA74`) with spark icon, timestamp, and pasted summary snippet.

### Tier 4: The Acrylic Archive Rack (Bottom Drawer / Collapsible Section)

* Inspired by Ref 3's 3D angled glass cards.

* Houses projects moved to `projects/_archive/`. Cards sit stacked horizontally like frosted acrylic folders (`Templates`, `Compliance` look). Hovering fans the stack out to inspect legacy summaries and dated logs without polluting active KPIs.

---

## 4. Visual System & Tokens

### Color Palette

| Token | Hex / Value | Usage |

| :--- | :--- | :--- |

| `--bg-canvas` | `#090A0F` | Deep obsidian base background |

| `--bg-surface-glass` | `rgba(22, 24, 34, 0.65)` | Main folder containers & cards (`backdrop-filter: blur(20px)`) |

| `--border-specular` | `rgba(255, 255, 255, 0.12)` | Top/left 1px highlight on glass cards and bento widgets |

| `--glow-attention` | `radial-gradient(circle, #FF5E62 0%, #FF9966 45%, transparent 80%)` | `Needs Attention` & blocker telemetry |

| `--glow-active` | `radial-gradient(circle, #34D399 0%, #059669 50%, transparent 80%)` | `Active` status, healthy velocity, Ref 3 acrylic highlight |

| `--glow-untracked` | `radial-gradient(circle, #E879F9 0%, #9333EA 50%, transparent 80%)` | `Untracked Folders` discovery widget |

| `--glow-stale` | `radial-gradient(circle, #38BDF8 0%, #1D4ED8 55%, transparent 80%)` | `Going Stale` idle-day drift indicator |

| `--agent-claude` | `#C4B5FD` (Surface) / `#1E1B4B` (Text) | Claude Code timeline cards & badges |

| `--agent-codex` | `#BEF264` (Surface) / `#1A2E05` (Text) | Codex timeline cards & badges |

| `--agent-chatgpt` | `#FDBA74` (Surface) / `#431407` (Text) | ChatGPT manual log cards & badges |

### Typography

* **Display & KPI Numerals:** `Inter`, `Plus Jakarta Sans`, or `SF Pro Display` — Light/Regular weights (`300`–`400`) at large scales (`2.75rem`–`4rem`) with tight tracking (`-0.04em`) for hero numbers (`68,575`, `28/135`, `87%`).

* **UI & Project Titles:** Medium/SemiBold (`500`–`600`), high legibility (`#F8FAFC` primary, `#94A3B8` secondary).

* **Logs, Paths & Next Steps:** `JetBrains Mono` or `Fira Code` (`0.82rem`) — used for file paths (`projects/_template.md`, `~/.claude/CLAUDE.md`), idle day counters, and terminal session excerpts.

---

## 5. Micro-Interactions & Functional Workflows

1. **One-Click Untracked Onboarding:**

   * When `sync.py` detects recent work in `~/.claude` or `~/.codex` for an unindexed directory, the **Untracked Folders** bento widget pulses softly. Clicking `Initialize` scaffolds `projects/<folder-name>.md` from `projects/_template.md` and pre-populates the initial agent log.

2. **Stale Project Triage:**

   * Projects in the `Going Stale` state display an inline quick-action bar on hover: `[Log Next Step]`, `[Move to Occasional]`, or `[Move to _archive/]`.

3. **ChatGPT Quick-Log Drawer:**

   * Because ChatGPT has no local session history for `sync.py` to read, clicking `+ Paste ChatGPT Summary` opens a frosted-glass modal that appends a properly formatted dated entry directly into the target `projects/<name>.md` file and triggers a dashboard refresh.

4. **Source-of-Truth Deep Linking:**

   * Clicking a project card's doc links opens the canonical repository documentation directly (local IDE URI `vscode://file/...`, `cursor://file/...`, or GitHub URL), preserving the core rule that **project repos remain the source of truth** while `PROGRESS-CHECK` acts as the high-altitude index.

