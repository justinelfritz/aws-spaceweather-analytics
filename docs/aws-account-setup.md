# AWS account setup checklist (manual, console/CLI)

These are the section 0/1 items from the project to-do that require console
access or credentials I don't have. Do these before running `cdk deploy` for
the first time.

## Cost guardrails (do first)

- [x] AWS Budgets: create a monthly cost budget (suggested: $30) with an
      email alert at 80% and 100% actual, plus a forecasted-spend alert.
      Console: Billing and Cost Management → Budgets → Create budget.
- [x] CloudWatch billing alarm as a second layer: enable "Receive Billing
      Alerts" (Billing preferences), then create a CloudWatch alarm on the
      `EstimatedCharges` metric (must be created in `us-east-1`).

## IAM

CDK's own security model is "bootstrap creates scoped roles, your everyday
credentials only assume them" — so this is a two-phase setup: one bootstrap
run with a broad identity, then a narrow day-to-day deploy user.

### Phase 1 — one-time bootstrap (needs a broad identity)

- [x] Install the AWS CLI v2 if you don't have it (`aws --version` to check).
- [x] Create a temporary IAM user, e.g. `cdk-bootstrap-admin`, with the
      `AdministratorAccess` managed policy attached. This is acceptable for
      a dedicated personal sandbox account (per the to-do's own note) and
      only needs to exist long enough to run bootstrap. Generate an access
      key for it.
- [x] Configure a CLI profile for it: `aws configure --profile sw-bootstrap`
      (paste the access key/secret when prompted; set your preferred region).
- [x] Find your account ID: `aws sts get-caller-identity --profile sw-bootstrap`
      — `450649088775`, `us-east-1`.
- [x] Run bootstrap once:
      `npx aws-cdk@latest bootstrap aws://<ACCOUNT_ID>/<REGION> --profile sw-bootstrap`
      (run from `infra/`). This creates the `CDKToolkit` CloudFormation stack:
      an S3 bucket, an ECR repo, and four IAM roles (deploy, lookup,
      file-publishing, image-publishing) plus a `CloudFormationExecutionRole`
      that defaults to `AdministratorAccess` — the role CloudFormation itself
      assumes to actually create resources. Done.
- [ ] Once phase 2 below is working, deactivate (don't need to delete yet)
      the `cdk-bootstrap-admin` access key — you'll only need it again for
      `cdk bootstrap` re-runs (new region, or a CDK major version bump).

### Phase 2 — day-to-day deploy user (narrow, what you'll actually use)

- [x] Create an IAM user, e.g. `cdk-deployer`, with **no** managed admin
      policy. Instead attach the policy in
      [`infra/iam/cdk-deployer-policy.json`](../infra/iam/cdk-deployer-policy.json)
      (account ID/region already filled in) — it only grants
      `sts:AssumeRole` on the four bootstrap roles created above, so this
      user can trigger deploys but has no standing permissions of its own
      (CloudTrail will show every action as the assumed role, not this
      user, which is the point).
- [x] Generate an access key for `cdk-deployer` and configure a second CLI
      profile: `aws configure --profile sw-deploy`.
- [x] From now on, deploy with
      `npx aws-cdk@latest deploy --all -c stage=dev --profile sw-deploy`
      (also works for `cdk diff`, `cdk destroy`). Verified with `cdk diff`
      across all 6 stacks — assume-role chain works end-to-end.
- [ ] For CI (GitHub Actions) later: prefer an OIDC identity provider + IAM
      role instead of long-lived access keys in GitHub secrets.

### Remaining decisions

- [ ] Decide dev/prod split: same account with a `Stage` tag + `-c stage=`
      context (what `infra/app.py` is already set up for), or two separate
      AWS accounts. Same-account-with-tags is enough to demonstrate the
      practice for a portfolio project; separate accounts is more realistic
      but adds setup overhead.
- [x] Enable CloudTrail (management events, at minimum) for audit logging —
      trail `space-weather-trail` (multi-region, logging management events)
      delivering to `s3://space-weather-cloudtrail-logs-450649088775`
      (public access blocked, SSE-AES256 default encryption). Created via
      CLI with the `sw-bootstrap` profile.

## Tagging convention

Already wired into `infra/app.py`: every stack gets `Project=space-weather`
and `Stage=<dev|prod>`. Extend with per-resource tags as needed once stacks
have real resources.

## Once this is done

Tell me and I'll run `cdk bootstrap` / `cdk deploy` (or walk you through it)
against the account, and we can move on to section 3 (ingestion Lambdas).
