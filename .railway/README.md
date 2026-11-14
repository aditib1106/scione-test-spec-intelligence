# Railway infrastructure

This named partial manages `scione-web` and its existing `scione-web-volume`
(500 MB, SFO), attached at `/data`. It also sets the Docker build, healthcheck,
restart policy and single replica. Variables are explicitly unmanaged.
Database services, source bindings and domains remain separately configured.

Keep the data volume and its attachment declared. Omitting a service's existing
volume attachment can propose a destructive detachment even when the plan says
"0 to destroy." Removing the volume resource after this partial owns it can
delete the volume. If its region or size changes, reconcile that change here
before applying another plan.

Install the pinned authoring SDK once:

```sh
npm ci --prefix .railway --ignore-scripts
```

From the repository linked to the intended Railway project and environment:

```sh
railway config plan --file .railway/railway.ts
```

Review the plan before running `railway config apply`. It must contain no volume
changes, deletions or destructive warning for a routine deployment update. Do
not rename the partial or remove its volume declaration without reviewing
ownership and the resulting plan.

Some CLI versions show a repeated `null -> ON_FAILURE` default-policy diff even
after the resolved service already uses that policy. Inspect the actual service
settings instead of repeatedly applying the same plan; see
https://github.com/railwayapp/cli/issues/1184.

The current CLI evaluates this file locally; deployment uploads do not apply
it automatically. Secrets belong in Railway variables, never this directory.
The old `railway.json` format has been replaced with the current IaC format.

Official references:

- https://docs.railway.com/infrastructure-as-code
- https://docs.railway.com/infrastructure-as-code/reference
