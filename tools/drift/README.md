# drift — CloudFormation Drift Detector

Manually checking for CloudFormation drift in the AWS console means clicking through multiple screens and decoding raw JSON diffs. `drift` runs detection, polls until it's done, and outputs a clean terminal summary. Pass `--out` to get a full markdown report with per-resource property deltas, remediation instructions, and a pre-formatted LLM context block ready to paste into Claude or Copilot.

---

## Prerequisites

```sh
brew install jq     # required
```

AWS CLI must be installed and credentials configured.

**Setup** — add to `~/.zshrc` (the installer does this automatically):

```sh
source ~/.drift.zsh
```

---

## Usage

**Check a stack for drift:**
```sh
drift my-stack-name
```

**Write a full markdown report to a timestamped file:**
```sh
drift my-stack-name --out
# → ./drift-my-stack-name-20240326-143022.md
```

**Write report to a specific file:**
```sh
drift my-stack-name --output ./reports/drift.md
```

**Override region and profile:**
```sh
drift my-stack-name --region us-west-2 --profile staging
```

**Pass a template filename directly (extensions stripped automatically):**
```sh
drift alb-target-group.yaml
# treated as stack name: alb-target-group
```

---

## Flags

| Flag | Description |
|------|-------------|
| `<stack-name>` | CloudFormation stack name (required) |
| `--region` | AWS region override |
| `--profile` | AWS named profile |
| `--out` | Write report to an auto-named timestamped file |
| `--output <file>` | Write report to a specific file path |

---

## Terminal output

When drift is detected, the terminal shows a color-coded summary:

```
[drift] detecting my-stack ... 12s elapsed
[drift] Detection complete — 2 resource(s) drifted in my-stack.
  • WebServerSG (AWS::EC2::SecurityGroup) — 2 property change(s)
  • CacheCluster (AWS::ElastiCache::CacheCluster) — DELETED (removed outside CFN)
```

- **Red** — resource deleted outside CloudFormation
- **Yellow** — properties changed
- **Green** — properties added

---

## Markdown report

The `--out` report includes:

1. **Summary table** — all drifted resources at a glance
2. **Resource deltas** — per-resource table showing expected vs. actual property values
3. **Remediation section** — YAML snippets and `aws cloudformation detect-stack-drift` commands to verify after fixing
4. **LLM context block** — a plain-text block formatted for direct paste into Claude or Copilot for remediation help

---

## No drift

If the stack is in sync, drift exits cleanly with no report:

```
[drift] No drift detected. Stack my-stack is in sync.
```
