# AI Study Assistant

A Telegram-based AI study companion that answers questions using **your own
course materials**. Study materials (typed PDFs — lecture notes, module
notes, assignments, lab manuals, previous-year question papers) live in a
GitHub repository; the bot syncs them, indexes them with SQLite full-text
search, and answers questions by prioritizing that material over the
language model's general knowledge.

## Features

- **Ask naturally** — explanations, definitions, comparisons, summaries,
  revision notes, interview questions, placement prep, MCQs, programming
  questions, code explanations, and marks-aware exam-style answers.
- **Follow-up aware** — "Explain DBMS" → "Advantages" → "Applications" →
  "Give MCQs" all stay scoped to DBMS; follow-up questions are anchored to
  the last real topic before searching, not just their own (often
  topic-less) wording.
- **Your materials come first, one source at a time** — every answer is
  built from the single best-matching PDF (ranked by subject match, module
  relevance, heading match, exact keyword match, phrase match, keyword
  density, and page relevance, in that order). A second source is only
  pulled in — and clearly labeled "Additional Reference" — when the primary
  document genuinely doesn't have enough material. If nothing in the
  repository matches, the bot says so explicitly before falling back to
  general knowledge; it never blends the two silently.
- **Clean citations** — every answer cites *Subject / Module / Page*
  (subject from the GitHub folder, module from the PDF filename or nested
  folder, page from the actual indexed page) — never a raw filename.
- **Marks- and MCQ-count-aware** — "10 marks" shapes the answer's length
  and structure; "25 MCQs" generates exactly that many, strictly from
  uploaded material (MCQs are refused, not improvised, if nothing matches).
- **Incremental GitHub sync** — only new/changed/deleted PDFs are
  re-downloaded and re-indexed, based on the repository's commit tree. Runs
  on startup, on demand (admin `/sync` or general `/refresh`), and
  optionally on a schedule (`SYNC_INTERVAL_MINUTES`) in the background
  without blocking FastAPI or the bot.
- **No OCR, no embeddings, no vector DB** — PDFs are typed/searchable text
  extracted with PyMuPDF; retrieval is SQLite FTS5 (BM25 + composite
  re-ranking). This keeps the whole system lightweight enough to run on a
  $7/month box for a 300-500 document knowledge base.
- **Multi-user, session-aware** — isolated conversation history, subject/
  module focus, and preferences per Telegram user.
- **Admin tooling** — `/sync`, `/syncstatus`, `/reindex`, and `/status` are
  restricted to `ADMIN_USER_IDS` and give full visibility into indexing
  health without needing shell access to the deployment.
- **Production-minded** — structured logging (per-subsystem log files plus
  a combined one), health checks, retries, graceful degradation (a failed
  GitHub sync or a single corrupt PDF never takes the bot down), Docker +
  Render deployment, automated tests.

## Architecture

```
Telegram User
     │
     ▼
python-telegram-bot (polling or webhook)
     │
     ▼
ConversationManager  ──────────────┐
     │                             │
     ├─▶ IntentDetector            │
     ├─▶ SearchEngine (FTS5/BM25) ◀─┤  SQLite (documents, sections,
     ├─▶ PromptBuilder             │  sections_fts, sessions, history)
     └─▶ OpenRouterService         │
              │                    │
              ▼                    │
        OpenRouter API             │
                                   │
GitHubSyncService ──▶ PDFProcessingService ──▶ IndexingService ──┘
     │
     ▼
GitHub repository (source of truth for study materials)
```

FastAPI hosts the whole process: it runs the Telegram bot (as a background
task in polling mode, or via a `/telegram/webhook` route in production), a
`/health` endpoint for monitoring, and owns the application lifecycle
(startup sync, graceful shutdown).

## Folder structure

```
app/
  config/           Environment-driven settings (single source of config)
  database/         SQLite connection, schema (incl. FTS5), models, repository (DAO)
  github_sync/      Incremental GitHub repository synchronization
  pdf_processing/    PyMuPDF text extraction + indexing orchestration
  search/           FTS5 search engine, intent detection
  llm/              Prompt builder + OpenRouter service
  telegram_bot/     Bot wiring, command/message handlers, conversation manager
  formatting/       Telegram Markdown formatting & message chunking
  caching/          Lightweight in-process TTL cache
  middleware/       FastAPI request logging + standardized error responses
  health/           Aggregate health checks
  utils/            Logging setup, exception hierarchy
  main.py           FastAPI app + application lifecycle
scripts/            Operational utility scripts (sync, backup, diagnostics, ...)
tests/              pytest suite (unit + integration-style tests)
data/               Runtime data: SQLite DB, synced PDFs, logs, cache, backups
```

## Study material repository layout

Two layouts are supported. **Flat** (module encoded in the filename):

```
DBMS/
  Module 1.pdf
  Module 2.pdf
  Question Bank.pdf
Operating-Systems/
  Module 1.pdf
  Module 2.pdf
```

**Nested** (module encoded as a subfolder — still fully supported):

```
Operating-Systems/
  Process-Management/
    lecture-notes-deadlock.pdf
Data-Structures/
  Trees/
    module-notes.pdf
```

In both cases, the **top-level folder is always the subject**. In the flat
layout, the module is derived from the filename — "Module 1.pdf" becomes
module "Module 1"; a non-numbered name like "Question Bank.pdf" is used
as-is. In the nested layout, the module is the middle folder name. Deeper
nesting beyond that is fine; only these levels are used for classification.
All PDFs must be typed/searchable — scanned image-only PDFs are not
supported (no OCR is performed, by design).

## Prerequisites

- Python 3.12+
- A Telegram bot token (see below)
- An OpenRouter API key (see below)
- A GitHub repository containing your study material PDFs

## Setting up a Telegram bot

1. Open a chat with [@BotFather](https://t.me/BotFather) on Telegram.
2. Send `/newbot` and follow the prompts to choose a name and username.
3. BotFather gives you a token like `123456:ABC-DEF...` — this is your
   `TELEGRAM_BOT_TOKEN`.

## Setting up OpenRouter

1. Create an account at [openrouter.ai](https://openrouter.ai).
2. Generate an API key at [openrouter.ai/keys](https://openrouter.ai/keys).
3. Pick a model slug (e.g. `openai/gpt-4o-mini`, `anthropic/claude-3.5-haiku`,
   `google/gemini-2.0-flash-001`) for `DEFAULT_LLM_MODEL`.

## Setting up the GitHub repository

1. Create a repository (public or private) and upload your PDFs following
   the `Subject/Module/file.pdf` layout above.
2. Set `GITHUB_REPO_URL` to its URL and `GITHUB_BRANCH` (usually `main`).
3. If the repository is **private**, create a GitHub fine-grained personal
   access token with read-only "Contents" permission on that repo, and set
   it as `GITHUB_TOKEN`.

## Local development

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and fill in TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY, GITHUB_REPO_URL, etc.

python scripts/validate_env.py  # sanity-check configuration
uvicorn app.main:app --reload   # starts the API + Telegram bot (polling mode)
```

With `TELEGRAM_USE_WEBHOOK=false` (the default), the bot uses long polling —
no public URL needed, ideal for local development. Message your bot on
Telegram to try it out.

### Running tests

```bash
pytest tests/ -v
```

The suite covers configuration, the database/repository layer, PDF
extraction (against real PDFs generated with PyMuPDF), FTS5 search, intent
detection, prompt building, the OpenRouter client (mocked HTTP transport —
no live network calls), response formatting, and the conversation manager
end-to-end (with a stubbed LLM).

## Docker deployment

```bash
cp .env.example .env   # fill in required values
docker compose up --build
```

This builds the image, starts the container with persistent volumes for the
database, synced PDFs, logs, cache, and backups, and exposes the API on
`API_PORT` (default `8000`). Check `http://localhost:8000/health`.

## Render deployment

1. Push this repository to GitHub.
2. In the Render dashboard, choose **New → Blueprint** and point it at your
   repo — it will pick up `render.yaml` automatically.
3. Render will prompt for the environment variables marked `sync: false`
   in `render.yaml` (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_URL`,
   `OPENROUTER_API_KEY`, `GITHUB_REPO_URL`, `GITHUB_TOKEN`). Set
   `TELEGRAM_WEBHOOK_URL` to your Render service's public URL (Render shows
   this in the dashboard; you may need one initial deploy to learn the URL,
   then update the env var and redeploy).
4. `TELEGRAM_USE_WEBHOOK` is set to `true` for the Render deployment —
   the app registers the webhook automatically with Telegram on startup.
5. A persistent disk is mounted at `/app/data` so the SQLite database and
   synced PDFs survive restarts and deploys.

## Environment variables

See `.env.example` for the full list with defaults and explanations. The
mandatory ones are `TELEGRAM_BOT_TOKEN`, `OPENROUTER_API_KEY`, and
`GITHUB_REPO_URL` — the app refuses to start without them.

Two more are worth calling out:
- `ADMIN_USER_IDS` — comma-separated Telegram user IDs allowed to run the
  admin commands below (`/sync`, `/syncstatus`, `/reindex`, `/status`). Find
  your numeric ID via [@userinfobot](https://t.me/userinfobot) on Telegram.
  Leave empty and those commands simply reply "You are not authorized to
  use this command." to everyone.
- `SYNC_INTERVAL_MINUTES` — set to e.g. `30` to sync automatically in the
  background on that interval. `0` (default) disables it; startup sync and
  the manual commands work regardless.

## Bot commands

| Command | Description |
|---|---|
| `/start` | Welcome message |
| `/help` | List available commands |
| `/about` | About this bot |
| `/subjects` | List indexed subjects |
| `/modules` | List modules for your current subject |
| `/history` | Show your recent questions |
| `/clear` | Clear your conversation history |
| `/refresh` | Re-sync study materials from GitHub |
| `/settings` | View your current subject/module focus and preferences |

**Admin-only** (requires your Telegram user ID in `ADMIN_USER_IDS`):

| Command | Description |
|---|---|
| `/sync` | Sync immediately; replies with new/updated/deleted/indexed counts |
| `/syncstatus` | Last sync time, repository commit, indexed PDFs/pages, DB status, duration |
| `/reindex` | Rebuild the FTS5 index from already-synced local PDFs — **no re-download** |
| `/status` | Full system status: app, GitHub, database, Telegram, OpenRouter, model, version |

You can also just type a question naturally — no command required. A few
examples of what shapes the answer:
- `"Explain normalization in DBMS for 10 marks"` — length/structure follows
  the requested marks (2/5/8/10/15 are recognized explicitly; other values
  round to the nearest).
- `"Give me 25 MCQs on binary trees"` — generates exactly 25, strictly from
  indexed material (never invented from general knowledge).
- `"Explain DBMS"` → `"Advantages"` → `"Applications"` → `"Give MCQs"` — each
  follow-up stays scoped to DBMS automatically.

## Utility scripts

```bash
python scripts/sync_repository.py [--force]     # manual sync + index
python scripts/rebuild_database.py --yes        # wipe and rebuild the whole index
python scripts/cleanup_cache.py [--logs]        # clear the in-process cache (+ old logs)
python scripts/cleanup_logs.py [--max-age-days] # delete old rotated log files
python scripts/optimize_database.py             # FTS5 optimize + ANALYZE + VACUUM
python scripts/validate_env.py                  # check configuration before starting
python scripts/diagnostics.py                   # full status report
python scripts/backup.py create                 # timestamped backup (DB + PDFs)
python scripts/backup.py list                   # list existing backups
python scripts/backup.py restore --file <path>  # restore from a backup (validated first)
```

`/sync` and `/reindex` (Telegram, admin-only) cover the same ground
interactively for a running deployment — `sync_repository.py` and
`rebuild_database.py` are for the command line / CI.

## Logging

In addition to the combined `app.log` and error-only `errors.log`,
`data/logs/` also contains per-subsystem files for focused debugging:
`bot.log`, `search.log`, `github.log`, `database.log`, and `openrouter.log`.
Every subsystem's entries still appear in `app.log` too — the per-subsystem
files are an additional, narrower view, not a replacement.

## Troubleshooting

- **Bot doesn't respond**: check `/health`, then `data/logs/errors.log`.
  Confirm `TELEGRAM_BOT_TOKEN` is correct and, in webhook mode, that
  `TELEGRAM_WEBHOOK_URL` is publicly reachable over HTTPS.
- **"This topic was not found in your uploaded study material" for
  everything**: run `/refresh` (or, if you're an admin, `/sync` /
  `/syncstatus`) — the repository may not have synced yet, or your PDFs may
  be under a different subject/module structure than expected (see
  "Study material repository layout" above).
- **MCQ requests get refused instead of answered**: this is intentional —
  MCQs are only generated from indexed material, never invented from
  general knowledge, so a topic with no matching content returns a
  "couldn't find enough material" message instead of a hallucinated quiz.
- **A specific PDF never shows up in answers**: it's likely scanned/image-only
  (unsupported by design) or corrupted. Check the latest sync's failed-files
  list via `python scripts/diagnostics.py` or the admin `/sync` reply.
- **`/status`, `/sync`, `/syncstatus`, or `/reindex` says "You are not
  authorized to use this command."**: add your numeric Telegram user ID to
  `ADMIN_USER_IDS` (comma-separated) and restart.
- **OpenRouter errors**: verify `OPENROUTER_API_KEY` and that
  `DEFAULT_LLM_MODEL` is a valid, currently available model slug on
  [openrouter.ai/models](https://openrouter.ai/models).
- **GitHub sync fails**: for private repos, confirm `GITHUB_TOKEN` has
  Contents read access; for large repos, note that a single sync call lists
  the whole tree — if you see a truncation warning in the logs, split the
  repository into more/smaller subdirectories.

## FAQ

**Can I use a different LLM provider?** Yes — the `OpenRouterService` is
intentionally the only place that talks to the model. OpenRouter itself
already gives access to most major providers under one API; to add a
genuinely different provider (e.g. a local Ollama instance), implement the
same `generate()` interface in a new service and swap it in `app/main.py`.

**Why SQLite FTS5 instead of a vector database?** At 300-500 documents,
keyword search with BM25 ranking is fast, requires no extra infrastructure,
and is easy to reason about. Embeddings/vector search were explicitly out of
scope for this project.

**Can I run this without Docker?** Yes — `uvicorn app.main:app` works
directly as long as `requirements.txt` is installed and `.env` is configured.

**How is conversation history limited?** `MAX_CONVERSATION_HISTORY` caps how
many prior turns are sent to the LLM per request; `/clear` wipes a user's
stored history entirely.

**Why does it only cite one source?** Normal questions are answered from a
single best-matching PDF by design — mixing partial paragraphs from many
documents tends to produce muddled, less trustworthy answers. A second
source only appears (as "Additional Reference") when the primary document's
retrieved content is too thin to answer from alone.

**How does ranking actually work if it's "just" FTS5?** FTS5's BM25 score
is the base signal; on top of it, `SearchEngine` adds weighted bonuses for
subject match, module match, heading match, exact keyword match, phrase
match, and keyword density, in that priority order, so a subject-matching
result always outranks a merely keyword-matching one from the wrong course.

## Contributing

Issues and pull requests are welcome. Please run `pytest tests/ -v` before
submitting, and keep new modules consistent with the existing layered
architecture (presentation → application → business logic → data →
infrastructure).

## Future enhancements

- Streaming responses back to Telegram as the LLM generates them
- Per-user export of conversation history as a downloadable file
- Optional Redis-backed cache for multi-instance deployments
- A `/broadcast` admin command to message all known users after a sync

## License

MIT — see [LICENSE](LICENSE).
