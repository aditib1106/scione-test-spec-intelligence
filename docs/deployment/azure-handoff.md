# Azure handoff: provisioning and acceptance plan

This is a migration plan, not a deployed Azure installation. The existing image
contains React and FastAPI; changing hosts does not require rebuilding the product
as separate frontend/backend services. Keep Railway running until the Azure copy
passes acceptance and the client agrees to switch.

## Ask the client to provide

- An Azure subscription and resource group owned by the client, with a named
  technical contact and agreed region/budget.
- Access for named team members through Azure roles scoped to that resource group,
  or a client operator who can perform the provisioning. Do not share a personal
  Azure password. Confirm any additional permission needed for role assignments.
- The intended Linux container host or server, a private MySQL service, persistent
  application storage, and the final HTTPS hostname. An existing Linux VM behind
  a locally managed reverse proxy is one possible starting point. Managed App
  Service or Container Apps needs its ingress and storage settings verified first.
- A decision about existing trial data: migrate it, or start fresh. Define who may
  receive account/database exports and where encrypted backups will be retained.
- Client-owned model-provider credentials, permitted endpoints and document-use
  terms. Hosting access does not include inference credits or model access.

## Current application contract

| Area | Required configuration |
| --- | --- |
| Image | Root Dockerfile; Linux container; pinned source commit and image digest |
| Process | Image default `python -m scione.deployment`; one worker and one replica |
| Network | Container port `8080`, public HTTPS, private database access |
| Public URL | `SCIONE_PUBLIC_URL=https://<client-host>`; correct explicit host/origin allowlists if configured |
| Application storage | Writable persistent workspace, normally `/data`; runtime UID/GID `10001` |
| Database | MySQL-compatible server; central account database plus one `scione_<lab-id>` schema per lab |
| Provisioning | Database principal can create/migrate the intended lab schemas; agree a narrower operator/runtime split before changing privileges |
| Secrets | Server-only environment/secret settings, including database URLs and selected provider key; never image layers or frontend variables |
| Operations | `/api/health` liveness plus authenticated database/file/export checks; backups, storage alerts and a named recovery owner |

The existing application state is described in [Railway deployment and handoff](railway.md)
and [storage lifecycle](../architecture/storage_lifecycle.md). Account passwords
are stored as hashes; migrate the account database to retain accounts, or explicitly
provision new accounts. Do not copy plaintext trial passwords into this document.

### Host-specific work to verify

- **Proxy trust:** hosted startup currently trusts Railway ingress, or loopback
  proxies on other hosts. A VM proxy uses that loopback path only when the
  networking actually preserves a loopback peer; a Docker bridge commonly presents
  a different address. VM and managed Azure ingress therefore need a reviewed
  trust configuration for their actual network path before deployment;
  setting a public URL alone does not solve forwarded client-IP/HTTPS handling.
  Do not set `RAILWAY_SERVICE_ID` to impersonate Railway or trust arbitrary clients.
- **Persistent files:** a container filesystem is not sufficient. For App Service,
  configure a persistent `/home` or mounted storage location and set
  `SCIONE_WORKSPACE_DIR` accordingly. Verify that the mounted filesystem permits the
  current startup ownership and UID-10001 writes, atomic file replacement and
  advisory locks. Startup changes only the mount root's ownership; restored child
  files must already be accessible to UID/GID 10001. A mount that disallows `chown`
  needs a startup adapter; this has not been Azure-tested.
- **Port and startup:** configure the host's container port as well as the app's
  `PORT=8080`. Preserve the image entrypoint so migrations and provisioning run
  when storage is mounted. In classic App Service custom-container configuration,
  `WEBSITES_PORT=8080` selects the routed container port; newer sidecar setups use
  their container-port configuration instead.
- **MySQL TLS:** Azure Database for MySQL Flexible Server enforces TLS by default.
  Configure the SQLAlchemy/PyMySQL connection with certificate validation and the
  supported CA chain, and verify central and per-lab connections. Do not disable
  secure transport merely to make an unverified URL connect.
- **Availability:** keep one replica running during this pilot. Analysis jobs and
  admission limits are process-local; browser navigation is safe, but container
  restarts do not resume interrupted inference. Scaling to zero or multiple
  replicas needs a shared job/admission design first.

Microsoft references: [custom containers and persistent storage](https://learn.microsoft.com/en-us/azure/app-service/configure-custom-container),
[App Service settings](https://learn.microsoft.com/en-us/azure/app-service/reference-app-settings),
and [MySQL TLS configuration](https://learn.microsoft.com/en-us/azure/mysql/flexible-server/security-tls).

## Migration sequence

1. Freeze an intended release, run local regressions and build the image. Provide
   source, lockfiles, image digest, environment-variable names and operator guides.
2. Provision an isolated Azure staging installation. Verify ingress, TLS, storage
   ownership, migrations and outbound provider access using synthetic data.
3. If retaining trial data, arrange a short write/analysis pause. Take consistent
   backups of **all** account/business schemas and the application workspace;
   restore both to staging. A Docker image alone does not contain this data.
4. Test two independent lab logins, maintainer switching, upload/extract/review,
   capability edit/delete, matching, clarification/history and PDF/JSON download.
   Try one short and one longer text PDF. Restart and verify persistence.
5. Check that unsigned requests and cross-lab file/report requests fail, and that
   public URLs, Secure cookies, CSRF and origin checks work through Azure ingress.
6. Let the client accept the staging copy, switch the hostname, and record the
   deployed image, migration time and recovery instructions. Retain an agreed
   rollback backup; avoid divergent writes to both installations after cutover.

## What is handed over

- Repository and release/image reference, reproducible build and deployment notes.
- A secret-variable inventory, delivered separately through the client's secret
  channel, plus rotation instructions and new owner responsibilities.
- Database and file backups if data retention is approved, with a demonstrated
  restore and a schema/account inventory.
- User guide, known limitations, evaluation artifacts, and release notes separating
  tested local work from actually deployed changes.

No Azure resource, subscription charge, DNS change or trial-data migration is
performed by following this planning document alone.
