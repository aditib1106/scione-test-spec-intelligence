# Railway trial deployment and handoff

The application is one Docker service serving React and FastAPI, with private
MySQL and a persistent application volume. Provider calls use a server-side API
key. No local database, uploaded document, password file or `.env` is included in
the image or CLI upload.

## Storage and accounts

| Component | Persistent location | Responsibility |
| --- | --- | --- |
| Account, session and login audit tables | Central MySQL database | Authentication and lab membership |
| Lab capability, spec records, reviewed facts and assessments | One `scione_<lab-id>` MySQL database per lab | Structured business records |
| Uploaded PDFs and immutable extraction/matching artifacts | `/data/tenants/<lab-id>` on the Web service volume | Source and developer evidence |
| Test catalog | Packaged application data | Maintainer-owned reference identities; read-only for labs |

The server derives lab ownership from the authenticated session. A browser cannot
choose another tenant by changing a request header, URL or body. Maintainers can
explicitly switch lab workspaces. Ordinary lab users share only their own lab's
records and cannot access developer artifacts or override inference settings.

Initial accounts come from `SCIONE_BOOTSTRAP_ACCOUNTS`, a server-only JSON secret.
Bootstrap creates missing accounts, provisions their storage and optionally seeds
one demonstration lab. It never resets an existing password or reseeds an
existing lab during restart. There is no public signup or email password reset.

Fresh preparation creates `admin` (maintainer) and four lab accounts, `lab1`
through `lab4`, in four independent workspaces. The maintainer initially opens
Lab 1 and can explicitly switch workspaces. Their generated passwords are private
and are not defaults. An existing private preparation file is retained unchanged;
preparation never renames, deletes or resets installed accounts.

The current trial uses one Web replica and one Uvicorn worker. Inference admission
allows two active document operations overall and one per lab. A larger deployment
requires shared inference leases and object storage before adding replicas.

## Prepare locally

From the repository root, with development dependencies installed:

```bash
uv run python scripts/prepare_deployment.py
```

This creates two ignored, owner-readable files:

- `local_data/deployment/private/account-credentials.json`: fresh initial trial logins.
- `local_data/deployment/private/railway-variables.json`: the selected provider's
  configuration, initial accounts and a reference to Railway's MySQL URL.

It does not deploy, call a model or change local MySQL. Re-running keeps the
generated passwords. Never paste either file into an issue, PR, terminal command
argument or chat. Only the platform's private variable editor should receive the
variables. Local preview credentials are separate from hosted credentials.

## Create the project

Use a Railway account whose billing and ownership can later be transferred to
the client. Review the current [plans](https://docs.railway.com/pricing/plans)
and [cost controls](https://docs.railway.com/pricing/cost-control). Hosting charges
and inference-provider charges are separate.

Check the account's [Trial and retention terms](https://docs.railway.com/pricing/free-trial)
before handoff. The current Trial ends after 30 days or $5 of usage, whichever
comes first, then moves to Free with $1 monthly credit. Railway deletes volumes
created by Trial accounts 30 days after the Trial credits expire. Upgrade to
retain that data and keep independent backups; a trial volume is not permanent
storage. Confirm the ongoing hosting plan and owner with the client.

1. Create an **Empty Project**. GitHub repository access is optional for CLI deploys.
2. Add an empty service called `scione-web`.
3. Add the MySQL database service, retaining its name `MySQL` so the prepared
   `${{MySQL.MYSQL_URL}}` reference resolves. Use private networking; do not expose
   the database through a public TCP proxy for application use.
4. Attach a volume to `scione-web`, mounted at **`/data`**. MySQL needs its own
   database volume, provided by the database template.
5. Generate a Railway service domain for `scione-web`, targeting **8080**. The
   application derives its HTTPS origin from `RAILWAY_PUBLIC_DOMAIN`. A custom
   domain can be added later without changing application code. Set
   `SCIONE_PUBLIC_URL` to its HTTPS URL and restart the Web service; update explicit
   host/origin allowlists if you have configured them.
6. Add the prepared variables to `scione-web` only. Do not copy your local
   `SCIONE_DATABASE_URL`; use the platform reference. If you use another MySQL
   service name, update the reference accordingly.

Before client testing, use a paid plan and expand the **existing MySQL volume to
5 GB**. The Trial's 500 MB volume filled during retained-artifact testing and
prevented MySQL from starting; this also made sign-in and report exports fail.
Increasing the plan does not necessarily resize an existing volume. Check the
volume's capacity separately. Railway bills occupied storage, not unused capacity;
confirm current [volume limits and pricing](https://docs.railway.com/volumes/reference).

The current small trial uses this MySQL start command:

```text
docker-entrypoint.sh mysqld --innodb-use-native-aio=0 --disable-log-bin --performance_schema=0 --innodb-buffer-pool-size=256M
```

Keep the original database volume attached when resizing or restarting. If MySQL
reports `No space left on device`, expand the volume before retrying startup;
do not delete database, redo or undo files. After recovery, verify an authenticated
lab read and a report export. `/api/health` alone does not establish database health.

The trial has one maintainer and four independent laboratory workspaces. Share
only the intended lab account with testers. Keep the maintainer account private.
The four current client-trial workspaces start with empty specification libraries
and the same fixed synthetic capability declarations; actual lab users may edit
or remove them. These declarations are demonstration data, not evidence of any
real laboratory's equipment or accreditation.

For a fresh installation, bootstrap seeds the 70-service development profile.
To append the additional 12 fictional coatings declarations used by the client
trial, run the initializer from the checkout through an existing operator SSH
connection for each target lab account (substitute `lab2`, `lab3`, and `lab4`).
The runtime image contains the installed package, not the checkout's scripts.
This sends only the initializer's code, not local data or credentials:

```bash
python - <<'PY'
import subprocess
from pathlib import Path
source = Path("scripts/initialize_lab.py").read_text()
prefix = (
    "from scione.deployment import prepare_host_environment, prepare_data_directory; "
    "from scione.config import Settings; prepare_host_environment(); "
    "prepare_data_directory(Settings()); "
)
subprocess.run([
    "npx", "--yes", "@railway/cli", "ssh", "--service", "scione-web",
    "python", "-c", prefix + "exec(" + repr(source) + ")",
    "--lab-user", "lab1", "--profile", "ms-pnt-410", "--merge-existing",
], check=True)
PY
```

The initializer retains existing declarations and SQL revisions; it does not
clear specifications. Removing a specification in the UI archives it, and uploading
identical bytes restores its prior analysis with an explicit receipt. A genuinely
fresh trial workspace must be provisioned separately by the operator. Never drop
SQL tables or delete the application volume to simulate a clean upload.

### Connect and deploy local code

The official CLI can run through npm without global installation:

```bash
npx --yes @railway/cli login
npx --yes @railway/cli link
```

Select the intended project, `production` environment and `scione-web` service.
`.railwayignore` and `.dockerignore` exclude private/runtime files. Do not use
`--no-gitignore`. The deployment uploads the current working tree, including
uncommitted source changes; confirm the intended branch and local tests first.

Install the pinned authoring SDK with `npm ci --prefix .railway --ignore-scripts`,
then preview the [infrastructure definition](../../.railway/README.md) with
`npx --yes @railway/cli config plan --file .railway/railway.ts`. Review it before
applying. The definition retains the existing Web data volume; never remove that
resource or its `/data` attachment during a routine update. Provider/account
values remain unmanaged by infrastructure source.

For a fresh clone or another client project, first inspect
`.railway/railway.ts`: it currently names `sweet-possibility` and explicitly
retains `scione-web-volume` in `sfo` at 500 MB. Adapt the project name and the
volume's actual name, region and size to that installation before planning.
Do not blindly apply this project's volume declaration elsewhere. A service
attachment removal can be destructive even if the plan says "0 to destroy";
check the detailed diff and destructive warnings. Never detach `/data`, shrink
or delete a data volume during an ordinary code release. Keep MySQL separately
managed, and do not add it to this partial without reviewing ownership.

The important runtime settings are:

| Setting | Value |
| --- | --- |
| Build | Root `Dockerfile` |
| Start | Image default: `python -m scione.deployment` |
| Port | `8080` |
| Health check | `/api/health`, timeout 300 seconds |
| Replicas / workers | `1` / `1` |
| Restart | On failure, at most three retries |
| Application volume | `/data` |

Then deploy:

```bash
npx --yes @railway/cli up --service scione-web --detach
```

The image installs hash-verified runtime dependencies from `uv.lock` and builds
the frontend using `package-lock.json`. The container validates configuration,
prepares the volume root, drops to UID
10001, performs additive migrations and provisions missing accounts before
serving. These steps run at **container startup**, because the persistent volume
is unavailable during build or pre-deploy commands. Do not substitute the legacy
`scione serve` command as the public entrypoint.

After the first successful startup, remove `SCIONE_BOOTSTRAP_ACCOUNTS` from the Web
service variables. Existing accounts remain in MySQL; a restart does not require
the plaintext bootstrap list. Retain the private operator file only as needed for
initial credential delivery, then rotate or remove it according to handoff policy.

## Verify the hosted trial

Use the generated HTTPS URL, not a direct container HTTP endpoint.

1. Open the login page in a private browser window. A business API request without
   a session must receive 401; the health endpoint is public.
2. Log in as the seeded lab, upload a synthetic PDF with a fresh filename, review
   extracted requirements and generate a report. Check PDF/JSON downloads.
3. Log out and sign in as the second lab. It must not see the first lab's file,
   capabilities or report. Uploading identical PDF bytes must still create that
   lab's own record with its own uploaded filename.
4. Log in as `admin`. Confirm explicit lab switching, developer diagnostics and
   the maintainer-only **Monitoring** page. Lab accounts must not access it.
5. Restart the Web deployment and verify files, lab changes and logins persist.

Production cookies are Secure/HttpOnly/SameSite, unsafe API requests require a
session CSRF token and an allowed origin, and API responses use `Cache-Control:
no-store`. The application's bounded in-memory navigation views are scoped to the
current session, revalidated on visits and cleared on writes or session changes.
Login attempts are rate limited. Password changes and disabled accounts revoke
existing sessions. Production API documentation is disabled.

## Updates, monitoring and recovery

Update source normally, run the relevant tests and redeploy `scione-web`. Model
changes use private server variables and a Web restart. Never deploy a model key
to the frontend. Application updates do not erase the database or mounted files.
Check that no analyses are active before restarting or deploying during a trial.
Page navigation and browser disconnection do not cancel admitted operations, but
the single-worker server does not resume interrupted work after a restart. Its
activity endpoints report an unfinished saved operation as completion unknown;
users must inspect saved results before choosing an explicit retry.

The browser uses a short `Prefer: respond-async` POST followed by operation-status
GETs. This avoids holding a silent HTTP request across minutes of model inference:
Railway's public edge closes requests with no transferred data after five minutes.
The healthcheck timeout is a separate setting. The bounded in-process worker pool
retains the original lab identity; it has two execution slots, one per lab, and no
waiting queue. Do not add replicas/workers without shared admission leases and
durable job state. See [Railway's edge limits](https://docs.railway.com/networking/public-networking/specs-and-limits).

The local preview and hosted service have independent accounts, SQL records and
file storage. Local uploads and password changes do not appear in the cloud;
stopping the local process does not stop the hosted Web service or MySQL.
Sharing a provider key between them does share the provider account's credit
and rate limits. Redeploying application code is not a data migration.

### Built-in Monitoring

Maintainers can view the current lab or all labs over a bounded date window.
The page shows recorded extraction/matching operations, completion counts,
durations, processed pages/tests, saved comparison outcomes and source-review
burden. It uses the existing application and SQL storage; no external monitoring
subscription or extra inference call is required. Its small queries and event
records still consume the existing hosting resources.

New operation records measure handler wall time. Older extraction artifacts may
only store the sum of provider-call durations; that value is shown separately,
and missing operation duration remains "Not recorded." Historical matching time
comes from saved run timestamps. Cached reuse is counted separately and excluded
from duration percentiles. Running and incomplete records are not successes or
failures. Historical failed attempts may be incomplete, so recorded completion
ratios do not establish the overall service failure rate.

Comparison totals describe the latest saved assessment per document in the
selected window. They measure reported coverage, not model accuracy or specimen
pass/fail. Source-review counts indicate pending verification, not a measured
extraction error rate. Use independently labeled evaluation data for accuracy.

Monitoring does not return source text, prompts, provider keys or original model
responses. Maintainer developer tools remain the separate interface for private
inference artifacts. Opening or refreshing Monitoring does not run a model.

Use Railway deployment logs, health status, service metrics and workspace usage
for operations. Logs deliberately omit passwords, database URLs and provider keys;
developer model artifacts remain tenant-scoped private data. Set an email alert
and a chosen hard spending limit in Railway. Reaching that limit may stop the
trial; it does not cap spending at Sail, Fireworks or another inference provider.

### Railway's native dashboard

The environment's native Observability dashboard can combine hosting usage and
monthly forecasts, Web latency/status/request counts, Web/MySQL CPU and memory,
volume disk usage, egress, application errors and slow HTTP requests. Use a one-hour
or 24-hour window when demonstrating a service with little recent traffic.
Hosting forecasts are estimates; remaining trial credit is displayed separately
by Railway. Model-provider credit and inference latency are not Railway hosting
metrics: use the provider's console for its balance and application Monitoring
for extraction/matching durations. Creating dashboard cards does not create a new
application service or call a model, but existing hosting and log retention still
follow Railway's plan.

### Reviewed report exports

Saving a human review updates the current web assessment without replacing the
machine findings. The current PDF/JSON export uses a separately versioned snapshot
that includes those saved decisions and specification-date metadata. Earlier
export links remain immutable. After saving, open **PDF & download** to view the
current export; a previously downloaded PDF is a fixed copy and does not update.

Back up **both** the MySQL volume and the Web `/data` volume. Enable scheduled
[volume backups](https://docs.railway.com/volumes/backups), take a manual snapshot
before risky migrations, and verify restoration into a separate environment.
Keeping only MySQL loses source files/artifacts; keeping only `/data` loses
structured records and accounts. Deleting a volume is destructive.

For operator account management, run `scione accounts --help`
inside the Web container with its private environment and mounted volume.
The hosted account command prepares the same HTTPS configuration and non-root
file ownership as application startup. Generated passwords must go to private
files rather than logs. A user can change
their password through the account menu. Disabling an account revokes its sessions.

To change a login name without replacing its account or password:

```bash
scione accounts rename --username previous.name --new-username lab1
```

The rename preserves the user ID, password hash, lab membership and saved data,
and revokes existing sessions. Update the username in the operator's private
credentials record and sign in again. Apply a desired rename independently to
local and hosted installations; they do not synchronize accounts.

## Client handoff

Transfer the Railway project/workspace to the agreed client owner, grant repository
access, document the deployment branch and rotate provider keys and initial
maintainer credentials. Review [project ownership](https://docs.railway.com/projects)
requirements before transfer. GitHub automatic deployment can be connected later
by an owner who can grant the Railway GitHub App access to the team repository;
CLI deployment does not require moving or forking it.

Deliver the infrastructure definition, this runbook, a database/data-volume backup,
provider ownership details and tested recovery steps. Personal student credits are
not a permanent hosting dependency. This release supports an authenticated trial;
it does not add public onboarding, SSO, email delivery, formal quote approval or
multi-region/high-availability operations.
