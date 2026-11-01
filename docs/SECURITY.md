# Security

## Current state (MVP) ✅

- **No cloud access.** The MVP makes zero AWS or network calls; boto3 is not a dependency.
- **No secrets.** None are needed. Only `.env.example` is committed, and `.gitignore`
  excludes `.env*` and `.streamlit/secrets.toml`.
- **Synthetic data only.** The loader refuses any dataset whose metadata is not labelled
  `synthetic`, so real data cannot be loaded by accident.
- **Safe parsing.** YAML uses `yaml.safe_load`, which constructs no arbitrary objects.
  Input records are validated by Pydantic with `extra="forbid"`.
- **Read-only by design.** The tool only produces recommendations. It has no code path
  that changes infrastructure.
- **CI permissions.** The GitHub Actions workflow runs with `contents: read`.

## Planned AWS adapters ⏳ (post-MVP, not implemented)

The adapters will be read-only and use a dedicated IAM role. Minimum permissions:

| Action | Why |
|---|---|
| `ce:GetCostAndUsage` | daily cost history (each Cost Explorer API request is billed) |
| `cloudwatch:GetMetricData` | hourly CPU |
| `ec2:DescribeInstances`, `rds:DescribeDBInstances` | inventory, instance types, tags |

No `*:Modify*`, `*:Stop*` or write actions. Credentials come from the standard AWS
credential chain (profile or role), never from code or the repo. If real data cannot be
fetched, the adapter will fail loudly; it will never fall back to synthetic data.

## Planned LLM explanation ⏳ (optional, off by default)

It sends only the structured recommendation JSON (no credentials, no raw account data),
takes its API key from an environment variable, and is tested so that every number in
its output appears in the input.
