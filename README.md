# SciOne Test Specification Intelligence

CMU Heinz Capstone: a lab-facing customer-specification archive and capability-gap pilot.
React, TypeScript and MUI provide the interface; FastAPI handles extraction, comparison,
storage and PDF generation. Streamlit remains a legacy compatibility tool.

## Quickstart: local preview with login

Use Python 3.11+, Node.js 22.12+ and uv. From the repository root:

```bash
uv sync --extra dev --locked
npm --prefix frontend ci
npm --prefix frontend run build
uv run python scripts/serve_trial.py
```

Open **http://127.0.0.1:8000**. Generated login details are saved privately in
`local_data/trial-preview/private/account-credentials.json`. Open that file locally
to sign in; never commit or publicly share it. The preview includes a maintainer,
two accounts sharing the coatings lab, and an account for a separate materials lab.
The coatings lab starts with synthetic development capabilities; the other lab starts
empty. Their files and records are isolated.

Fresh previews and deployment preparation use these usernames; passwords are generated
independently and appear only in the corresponding private credentials file:

| Username | Role | Initial workspace |
| --- | --- | --- |
| `admin` | Maintainer | Coatings lab; can switch labs |
| `lab1` | Lab user | Coatings lab |
| `lab1staff` | Lab user | Same coatings lab as `lab1` |
| `lab2` | Lab user | Separate materials lab |

This preview uses separate embedded SQL storage and leaves existing local data and
MySQL unchanged. Local and hosted accounts, passwords, databases and uploaded files
are independent, even when their usernames match. Documents do not synchronize between
them. Reusing the same provider key does share that provider account's credit and limits.
It requires no Docker. Stop with Ctrl+C; restart the same command to
keep saved data. Use `--port 8001` if port 8000 is occupied. This command does not
publish a link for external users.

For an independent rehearsal, use `--workspace-dir local_data/my-rehearsal --port 8001`.
This creates separate accounts and records; it does not clear an existing preview.
Keep private preview workspaces under ignored `local_data` or outside the checkout.

Specification versions are labeled by their printed specification date, separately
from upload time. See [specification date storage and migration](docs/deployment/specification-dates.md).

For AI analysis, create `.env` from `.env.example` only if `.env` does not already
exist, then fill a provider key locally. Preserve existing keys/database settings.
Lab users choose **Analyze PDF** or **Generate report**; the server selects the
configured model. Restart the preview after changing keys. Browsing, reviewing and
exporting saved results do not call a model.

## Accounts and workspaces

| Role       | Available workflows                                                                                          |
| ---------- | ------------------------------------------------------------------------------------------------------------ |
| Lab        | Customer specifications, source review, own lab capability, read-only test catalog, gap reports and exports  |
| Maintainer | Lab workflows plus lab workspace selection, Monitoring, developer tools, model settings and original inference artifacts |

The API enforces roles and lab isolation. Sessions use HttpOnly cookies, CSRF-protected
changes and Secure cookies in production. Login, logout and workspace changes clear
previous browser state, including other open tabs. Ordinary lab users cannot access
developer routes or modify the shared catalog.

Lab users can add, edit and delete their own capabilities. Choose a searchable
reference test or create a private test with optional private method codes; enter
conditions and reportable measurements as structured fields. Private identities
remain in that lab's SQL storage and participate in its matching candidates.
Formatting differences such as `TEST-123` and `test123` are recognized for private
codes; distinct methods and standard editions are not treated as equivalent.
Deleting a capability updates future comparisons while retaining saved reports
and versioned declarations. Existing installations receive an additive lab-storage
migration; no shared catalog or previous report is rewritten.

Each lab has a separate source/artifact directory and SQL storage: independent MySQL
schemas when configured, or separate embedded databases in the preview. Accounts and
sessions are stored centrally. This supports separate lab workspaces; multi-lab
recommendations, self-registration, email password reset and formal quote approval are
not implemented. Operators provision accounts and reset passwords with `scione accounts`.
Existing installations keep their accounts when restarted. The operator's
`scione accounts rename` command changes a username while preserving its identity,
password and lab records; it revokes existing sessions. Update the private login record
after renaming. Renaming a local account does not rename its hosted counterpart.

For hosted setup and handoff, see the [Railway deployment guide](docs/deployment/railway.md).
The Docker entrypoint supports an authenticated pilot with **one replica and one worker**.
A checkout or local preview does not mean a public deployment is already live.

## User workflow

| Area                              | Purpose                                                                                                                                                                                          |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Specifications → Library**      | Save customer/company metadata and revisions; search, view or remove a saved version. Expand detailed requirements when needed.                                                                  |
| **Specifications → PDF & review** | Compare extracted test items, test methods, conditions and acceptance criteria against highlighted PDF evidence. Confirm, edit, remove or add a missing source-backed test.                      |
| **Gap report**                    | Select a test item, inspect its assessment, and expand Conditions or Acceptance criteria for detailed comparisons. Filter and prioritize items needing attention. PDF/JSON exports are optional. |
| **Lab capability**                | Maintain supported test methods, conditions, reportable outputs and optional prices. Typed fields support values, ranges, units and context, including custom parameters.                        |
| **Test catalog**                  | Read shared test/method identities and aliases. A catalog entry does not establish lab capability or method equivalence.                                                                         |
| **Developer Tools**               | Maintainer-only run history, original provider output, diagnostics and maintenance.                                                                                                              |
| **Monitoring**                    | Maintainer-only operation counts, recorded durations, saved comparison outcomes and source-review burden for the current lab or all labs.                                                        |

**Generate report** from source review starts the configured-model comparison. Browsing,
expanding rows and changing filters never start inference. Progress shows elapsed time
and, when available, a reference from previous completed runs.

Monitoring is an operational view, not a model evaluation. New records measure handler
operation wall time; historical extraction records may contain only summed provider time,
which is displayed separately rather than substituted for missing operation durations.
Historical matching durations use saved run start/end times. Cached reuse is separate
and excluded from operation-duration percentiles. Running or incomplete operations are excluded
from recorded completion ratios, and older failure histories may be incomplete. Saved
matching coverage and review burden are not accuracy. This page uses existing app/MySQL
resources, adds no external monitoring service and makes no model calls.

Source confirmation verifies extracted information; it does not approve lab support.
Corrections save a reviewed-facts revision used by Library and matching, preserving the
original model output. Changed source rows require fresh confirmation. Detailed source
notes, sample plans and applicability are available on demand. The disabled OCR entry
marks the future scanned-document adapter and makes no API call.

**View extraction evidence** above the selected test opens saved PDF passages and
sample instructions. In a gap report, the information icon beside a result opens
**Why this result?** with the customer requirement, recorded lab capability, saved
assessment reason and available source evidence. Recorded provenance identifies AI
assistance or a field comparison when available. Opening these details does not run AI.

In Gap report, **Accept assessment** records the lab's review of the current findings.
**Edit decision** records a follow-up plan and note, such as customer clarification,
outsourcing or declining a test. Acceptance does not turn gaps or missing information
into matches. Reviews belong to the exact saved assessment; changed inputs require a
new review. Remove/restore changes report scope while keeping source data. Review
history is a separate JSON export; the PDF remains an immutable assessment snapshot.
Maintainers can access the older mapping/quantity workflow through **Gap report →
Report options → Review test matching**.

Within one lab, uploading identical PDF bytes reopens its saved record using the
current upload's filename, preserving company/version metadata and earlier analyses.
**Analyze again** creates a new extraction run, not a customer revision. Save a new
customer document under the existing specification family for revision history.
Removing a version hides it from the normal Library; files and historical reports
remain stored, and re-uploading the same PDF restores it.

Unknown prices, quantities, partner costs and lead times are not invented. A capability
match means the lab can perform and assess the request; it does not predict whether
the customer's material will pass. Reading full referenced standards is outside the MVP.

## Developer setup and model configuration

`uv run scione serve` is the **legacy loopback development server** and uses no login
by default. Keep that mode local. Use `scripts/serve_trial.py` for account/isolation
checks and the deployment guide for external hosting. Build the frontend before
starting the server. For frontend development, run `npm --prefix frontend run dev`
in another terminal; port 5173 proxies `/api` to port 8000.

Configure provider keys in the ignored `.env`, preserving existing values. Keys stay
server-side. Restart Python after changing settings.

| Provider  | Key variable        | Configured model default                             |
| --------- | ------------------- | ---------------------------------------------------- |
| Sail      | `SAIL_API_KEY`      | `zai-org/GLM-5.3`                                    |
| Fireworks | `FIREWORKS_API_KEY` | `accounts/fireworks/models/deepseek-v4p1-flash-1017` |

`MODEL_PROVIDER=sail` is the configured baseline. Fireworks, Groq and Gemini remain
alternatives. Lab accounts use server-selected settings; maintainer experiments live
in PDF analysis settings and **Gap report → Developer report tools → Run AI matching**.
Changing a selection does not call a model, and saved metadata describes the model
that actually produced the run. No dedicated model deployment or local GPU is needed
for these HTTP adapters. See the official [Sail quickstart](https://docs.sailresearch.com/quickstart)
and [Fireworks quickstart](https://docs.fireworks.ai/getting-started/quickstart).

The Sail baseline uses temperature 0, low reasoning, a 12,288-token completion ceiling
and a 300-second local HTTP deadline. Account/model limits still apply. Available
credit and valid JSON do not establish accuracy or guarantee zero cost. A provider
may finish and bill an accepted request after the browser closes or local waiting
ends. Idle pages never call a model.

The same GLM-5.3 baseline can also use Fireworks serverless inference. Set
`MODEL_PROVIDER=fireworks`, `FIREWORKS_MODEL=accounts/fireworks/models/glm-5p3`,
`FIREWORKS_REASONING_EFFORT=low`, `FIREWORKS_MAX_OUTPUT_TOKENS=12288` and
`FIREWORKS_TIMEOUT_SECONDS=300`, with a private Fireworks key. This is an explicit
operator choice, not automatic failover or a retry. Stored runs record the actual
provider and model; a provider change still requires evaluation of its outputs.

Default guided extraction makes two planned requests; compact extraction makes one.
Complex matching preflights complete-test batches, with at most eight requests and
up to sixteen for workspace PDFs longer than ten pages. Sail/Fireworks long-document
waves default to four concurrent calls; `SCIONE_LONG_DOCUMENT_PARALLELISM` accepts
1–4 and can be lowered to 1 or 2 for tighter provider quotas. Fitting-source inventory
and extraction remain serial, shorter matching batches retain their existing
two-call wave limit, and other providers remain sequential.
There is no automatic repair call, retry loop, provider fallback or durable job queue.
Failed analysis preserves the previous accepted result; maintainer artifacts retain
the failed attempt. A retry is a new explicit action.

Accepted analysis and matching continue while navigating between pages. The workspace
banner tracks active work; returning to the document or reloading reconnects to the
server's status rather than starting another model request. Closing a browser does
not cancel work already admitted by the server. This is still a single-worker pilot,
not a durable queue: a server restart can interrupt work. An unfinished saved status
then asks the user to check retained results before explicitly trying again.
The browser submits each analysis once with `Prefer: respond-async`, receives a
202 operation receipt, then reads progress and the saved outcome through short GETs.
It does not keep a single HTTP connection open for the whole model run. Submission
IDs are bound to the lab, document, action and managed settings. At most two jobs
run at once, with one per lab and no waiting queue. Finished receipts are retained
in memory for up to an hour (at most 128); saved business results remain in storage.
Connection loss triggers read-only reconciliation, never an automatic inference retry.
If observation cannot recover, the UI reports completion unknown rather than claiming
that the model failed. A confirmed saved success clears older transport errors.
The progress bar counts returned AI steps against the planned total; it is not a
time estimate. Checking and saving remain visible before the accepted result appears.
Separate timing references use completed runs with comparable workloads.

Repeated library reads reuse bounded, per-lab source derivations. Source, review,
facts and policy changes invalidate them; SQL metadata and edit versions remain live.
An additional process-local source-proof cache retains at most eight small-PDF
derivations, keyed by the complete requirement JSON, document JSON and PDF bytes.
It has no lab capability, account or session inputs, returns detached copies, and
writes nothing to disk. Changed source facts cannot reuse an older key.
The browser also keeps a small, session-only navigation cache while revalidating
against the server. Writes invalidate it, and login, logout or lab switching clear
it. Customer contents are not added to persistent browser storage.

Input guards are 60,000 characters for extraction and 120,000 for matching, including
prompts/context/schema. Oversized compact extraction automatically plans bounded
source-page chunks, retaining original page numbers and raw shared conditions/annexes.
Dense multi-page windows are made smaller to reduce completion-token pressure.
Explicit subsection references add exact raw section excerpts when uniquely located.
Primary/shared pages remain whole, and every original page is supplied whole in its
primary window. `SOURCE_VIEW` marks excerpted context; the manifest records original
page numbers and zero-based, half-open character offsets for each unchanged fragment.
Chunk prompts retain every parameter ID/label without repeating vocabulary descriptions.
`PAGE_ROLE` labels distinguish primary from context pages; the prompt requires a
substantive primary test row/request, rather than assigning context rows to a primary
section heading. Continued rows can use context, and explicit additional annex tests
remain eligible. Individual invalid inventory attachments are unused as whole entries
with their original indexes audited; independently valid, source-located tests survive.
If full chunk guidance cannot fit, it tries source-located primary/context names, then
primary names only, then no guide. Guidance evidence must occur in the supplied view,
including its exact excerpts. A requirement quotation located only in omitted context
text fails the chunk's scope check. These hints never alter the planned source view.
For PDFs longer than ten pages, Sail/Fireworks extraction uses the configured
long-document wave width (default four, maximum four); fitting short analysis stays serial.
Every page is covered, and every request is checked before inference. There are at most
eight extraction chunks plus one optional whole-source inventory call. All chunks must
succeed before replacing a saved extraction; failed attempts retain their artifacts.
Matching keeps its 120,000-character request guard and all candidate facts. Repeated
lab claim bodies use lossless references with a verified full round trip. The workspace
allows up to sixteen matching batches for PDFs longer than ten pages; other inputs keep
the eight-batch cap. All batches are preflighted before any matching request is sent.
An indivisible page/shared-context block or a plan exceeding the call budget fails
without silently truncating source. Legacy focused extraction still repeats the full
source. Scanned and handwritten PDFs require the separate OCR integration. See the
[inference policy](docs/architecture/inference_policy.md) for settings, validation,
recovery and shutdown behavior.

### Legacy local development with MySQL in Docker

This uses existing single-workspace development data; it does not create trial accounts
or migrate that data into an authenticated lab. Open Docker Desktop yourself, then:

```bash
uv run python scripts/setup_database.py
docker compose --env-file .env.database up -d --wait
uv run scione database-upgrade
uv run scione serve
```

The setup script creates a private, ignored `.env.database`, preserves an existing file,
and leaves `.env` unchanged. The app reads both env files. MySQL listens on
`127.0.0.1:3307`; no host MySQL service is needed. Open **http://127.0.0.1:8000**.
Stop Python with Ctrl+C. To remove the database container while retaining its volume:

```bash
docker compose --env-file .env.database down
```

The volume and image remain. `--volumes` permanently deletes database data; export
needed records first. A system-wide Docker prune is unnecessary. Without
`SCIONE_DATABASE_URL`, legacy development uses `local_data/library.sqlite3`.
Changing a connection URL does not migrate existing records.

## Data, extraction and matching

The shared catalog loads automatically: 99 synthetic test identities, 154 active
methods and a separate searchable index retaining all 32,000 synthetic method-bank
rows. Matching receives bounded, relevant candidates; the bank is not a source of
lab capability or proof that different test methods are interchangeable.

AI extracts atomic requirements with source evidence, preserving parameter identity,
role, stage, operator, values/ranges/tolerances, units, source wording and page. The
source-reading guide is partial, never a hard whitelist or replacement for the PDF.
Older runs can use conservative local parsing; unresolved wording stays available.
Document-verified derived views can exclude an exactly proved foreign-section
inheritance, add a uniquely owned physical table-cell citation, or complete the
three explicit facts in a closed conditioning sentence (duration, temperature and
relative humidity). These steps preserve original records and retain the complete
source/proof actions. Reviewed conflicts, exceptions, ambiguous rows and unproved
quantities remain reviewable; no test-specific value is guessed.
A cached geometric text view can improve legacy PDF reading order without changing
original bytes, saved documents or provider responses. It is not OCR or a guarantee
that every PDF layout is read correctly.

AI matching proposes links to supplied catalog/lab candidates and, where applicable,
wording equivalences. Code validates ownership, source evidence, complete coverage,
units, numeric ranges, stages and context. Decimal arithmetic determines numeric
comparisons; the model does not supply the final numeric verdict. Missing declarations
remain unknown, explicit limitations are gaps, and unfamiliar fields are not certified
from label similarity alone. Sample planning and workflow tasks remain distinct from
capability evidence. AI wording judgments remain fallible and require evaluation.

One narrow comparison proof can reuse a selected capability's unique reviewed CH
exposure mode for its own duration field. It requires the same explicit ISO 6270-2
method, original customer context and matching exposure stage/configuration/sequence.
Only a comparison copy receives the already declared context; durations are unchanged
and remain subject to numeric comparison. A method code alone supplies no setup.

Per-lab SQL stores companies, specification families/versions, extraction records,
requirements, atomic claims, lab revisions, comparisons and assessment reviews. Fixed
relationships use relational columns; variable details use JSON. Source files and
artifacts live under the configured workspace root, separated into authenticated
`tenants/<lab_id>/` directories. Lab revisions, model attempts and comparison snapshots
preserve their history. Original responses and raw matching artifacts are available
only to maintainers. See the [storage notes](docs/architecture/structured_archive.md).

The legacy React lab editor imports an existing `lab.json` once into SQL; that file
remains for recovery rather than serving as a second live source. Streamlit retains
older file-based behavior and is not the authenticated hosted interface.

### Development data and evaluation

To initialize the synthetic lab in a **legacy development workspace** deliberately:

```bash
uv run python scripts/initialize_lab.py
```

Normal startup preserves declarations. Replacement requires `--replace-existing` and
preserves prior SQL revisions. The frozen development lab has 70 exact-method services,
1,681 structured fields and explicit unavailable outputs. Its limits are independently
authored synthetic capability data, not customer acceptance values copied into the lab.
There are no sample-loading buttons in the user workflow.

For the original MS-PNT-410 PDF, a separate fixed profile contains twelve fictional
coatings services with intentional matches, range gaps and unknown declarations.
In an accounts-enabled local workspace, an operator can add them to a test lab:

```bash
.venv/bin/python scripts/initialize_lab.py --profile ms-pnt-410 --lab-user lab1 --merge-existing
```

The command uses the configured workspace/database. `--merge-existing` appends missing
capability IDs and preserves existing services, including CSP-EN-301 declarations and
user edits. Nothing is seeded automatically or copied into another lab. The profile's
capabilities and prices are synthetic, not client data or validated evaluation truth.

Upload the team's readable PDFs when available under `docs/Generated Customer Spec/`,
or use the bundled `docs/samples/CSP-EN-301_demo_spec.pdf` and
`docs/samples/MS-PNT-410_demo_spec.pdf`. Generated-corpus files remain local and may need
to be obtained from the team. Runtime catalog metadata and offline fixtures do not
depend on those local files.

Internal frozen-reference checks and successful walkthroughs are **not client-validated
accuracy claims**. Broad corpus matching still contains source-normalization, method-role
and missing-information issues. Model-authored reference annotations are development
aids, not independent production ground truth. See the [fixture guide](tests/fixtures/README.md)
for extraction/matching evaluation protocols and limitations.

## Tests and maintenance

```bash
uv run pytest
uv run ruff check src tests scripts
npm --prefix frontend run build
npm --prefix frontend run format:check
```

`npm --prefix frontend run test:e2e` starts disposable browser fixture servers.
`npm --prefix frontend run test:auth` checks real cookie login, two-lab isolation,
cross-tab account/workspace changes, report review and PDF export in a separate fixture.
Run browser suites sequentially because they share the test-output directory. Neither
suite uses your `.env`, keys or local workspace or calls a model. Set
`SCIONE_TEST_PYTHON` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE` when needed. On macOS,
temporary test paths must be canonical, without symlink traversal.

The offline semantic evaluator can replay saved attempts:

```bash
uv run python scripts/evaluate_semantic_matching.py \
  --run saved_batch_a.run.json saved_batch_b.run.json \
  --output local_data/experiments/semantic-replay
```

This is `CURRENT_POLICY_RECOMPILE`, not a reconstruction of historical verdicts.
For original artifacts use `scripts/evaluate_capability.py --artifacts` with independently
aligned labels. Without saved model runs, manual reference alignments test comparison
policy rather than model performance. Keep original attempts and score files unchanged.

Legacy embedded development supports **Developer Tools → Workspace maintenance → Reset**,
or `uv run scione reset-demo --yes` with the app stopped. This moves local lab/spec/report
files and the embedded database to private `.demo-backups/`. An external/MySQL database
blocks reset before changing data. Use a separate workspace/database for a fresh demo;
do not reset an authenticated client installation as a shortcut.

Packaged synthetic metadata lives in `src/scione/catalogs/data/`, including the lossless
method CSV `.csv.gz` and provenance manifest. The reviewed method-reference workbook in
`docs/Generated Customer Spec/` supports rebuilding via `python -m scione.catalogs.build_bank`.
Duplicate CSV/Excel sources, obsolete lab exports and generated corpus PDFs remain local.
Only active prompts are packaged; historical runs retain their captured requests.
Credentials, runtime data, backups, dependencies, builds and test output are ignored.
Only approved synthetic demo PDFs are allowlisted; client reference/meeting PDFs remain local.

## Deployment and handoff

The [Railway deployment guide](docs/deployment/railway.md) covers the Docker application,
managed MySQL, persistent application volume, HTTPS URL, server secrets and account
provisioning. React and the API share one origin. Keep the pilot to **one replica and
one worker**; durable jobs and multiworker leases are not implemented. A generated
hosting URL is enough for a trial; a custom domain can be added later.

The tracked `.railway/railway.ts` describes the current `sweet-possibility` project and
its existing 500 MB SFO web data volume. Adapt the project and actual volume settings
before planning a different installation. Review the complete plan and any destructive
warning; never detach an existing `/data` volume as a routine deployment update.

A hosted installation still needs verified backups of both SQL and source/artifact files,
restore checks, access review and client-owned hosting/model accounts for handoff.
The browser never receives database URLs or provider keys. Local tests do not establish
that public deployment is live or that corpus accuracy is validated. Personal student
credits are not a required hosting dependency.
