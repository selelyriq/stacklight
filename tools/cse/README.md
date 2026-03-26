# cse — CloudFormation Change Set Explainer

CloudFormation change sets show you _what_ is changing but not _what that means_. `cse` creates the change set and rewrites the output in plain English — surfacing risk levels, calling out replacements, and flagging data-loss scenarios. By default it **gates on any replacement or deletion** and exits non-zero, preventing accidental destructive deploys. Pass `--auto-approve` to let it through, or `--warn-only` to warn without blocking.

---

## Prerequisites

```sh
pip3 install boto3              # required
pip3 install anthropic          # optional — enables AI-powered explanations
```

AWS credentials must be configured (`aws configure`, environment variables, or an IAM instance profile).

---

## Usage

**Create a change set and explain it:**
```sh
cse --stack-name my-stack --template-file template.yaml
```

**Explain and execute (bypasses the destruction gate):**
```sh
cse --stack-name my-stack --template-file template.yaml --auto-approve --execute
```

**Warn about dangerous changes but don't block — good for adoption ramp:**
```sh
cse --stack-name my-stack --template-file template.yaml --warn-only --execute --no-interactive
```

**Explain an existing change set without creating a new one:**
```sh
cse --stack-name my-stack --describe-only --change-set-name my-cs
```

**Override AWS region and profile:**
```sh
cse --stack-name my-stack --template-file template.yaml --region us-west-2 --profile staging
```

**Pass parameter overrides:**
```sh
cse --stack-name my-stack --template-file template.yaml --parameters Env=prod DBSize=db.t3.large
```

---

## Flags

| Flag | Description |
|------|-------------|
| `--stack-name` | CloudFormation stack name (required) |
| `--template-file` | Path to YAML or JSON template |
| `--parameters KEY=VALUE` | Parameter overrides |
| `--capabilities` | IAM capabilities (default: all three) |
| `--change-set-name` | Custom change set name (auto-generated if omitted) |
| `--describe-only` | Explain an existing change set without creating a new one |
| `--execute` | Execute the change set after showing the plan |
| `--no-interactive` | Skip the confirmation prompt |
| `--auto-approve` | Allow deployments containing replacements or deletions (bypasses the gate) |
| `--warn-only` | Surface replacement/deletion warnings but do not block |
| `--region` | AWS region override |
| `--profile` | AWS named profile |
| `--no-color` | Disable ANSI color output |
| `--no-ai` | Skip Claude API, use built-in explanations only |

---

## Gating behavior

By default, `cse` **blocks and exits 1** if the change set contains any resource replacement or deletion:

```
  ✗  BLOCKED: this change set contains replacements or deletions

     REPLACEMENT: MyRDSInstance (AWS::RDS::DBInstance)
     REMOVAL:     OldQueue (AWS::SQS::Queue)

  Review the plan above, then re-run with --auto-approve to proceed.
```

The change set is preserved so you can inspect it or execute it manually after review.

### `--auto-approve`

Bypasses the gate and allows the deployment to proceed — equivalent to `terraform apply -auto-approve`. Use when you've reviewed the plan and are ready to deploy destructive changes:

```sh
cse --stack-name my-stack --template-file template.yaml --auto-approve --execute --no-interactive
```

### `--warn-only`

Shows a warning about replacements and deletions but does **not** block. The deploy proceeds normally. Useful when introducing `cse` to a team and building toward full gating:

```sh
# adoption ramp: warn the team but don't break the pipeline yet
cse --stack-name $STACK --template-file $TEMPLATE --warn-only --execute --no-interactive
```

Output:
```
  ⚠  WARN-ONLY: replacements or deletions detected (not blocking)

     REPLACEMENT: MyRDSInstance (AWS::RDS::DBInstance)
```

---

## Impact detail

For every replacement and deletion, `cse` prints a structured breakdown beyond the one-line explanation:

```
  DOWNTIME   5–15 min (longer for large instances or slow storage)
  LOSES    ‣ Database endpoint URL changes — update every connection string and env var
           ‣ Resource ARN changes — update any IAM policies or code referencing the old ARN
           ‣ Multi-AZ does NOT prevent this downtime — both primary and standby are replaced
  ROLLBACK   CFN attempts to restore the original instance if the update fails
  WHY REPLACED
     ‣ DBInstanceClass — always requires recreation  (triggered by parameter: DBSize)
```

- **DOWNTIME** — estimated service interruption
- **LOSES** — what changes or is permanently destroyed (data-loss items highlighted red)
- **ROLLBACK** — what CloudFormation does if the deployment fails mid-way
- **WHY REPLACED** — which specific properties trigger recreation and what caused them to change (parameter reference, resource reference, etc.)

---

## Attention block

All replacements and removals are surfaced in a summary box at the top of the plan, before the per-resource breakdown, so you see the danger immediately without scrolling:

```
  ┌─ ⚠  REQUIRES ATTENTION ─────────────────────────────────────────────┐
  │  REPLACEMENT   MyRDSInstance  AWS::RDS::DBInstance
  │    Downtime: 5–15 min
  │    ‣ Database endpoint URL changes
  │    ‣ ALL DATA WILL BE LOST unless DeletionPolicy=Snapshot is set
  │  REMOVAL       OldQueue  AWS::SQS::Queue
  │    Downtime: Immediate and permanent
  │    ‣ ALL MESSAGES IN THE QUEUE ARE LOST
  └──────────────────────────────────────────────────────────────────────┘
```

---

## AI mode

When `ANTHROPIC_API_KEY` is set, `cse` sends the change set to Claude for nuanced, context-aware explanations tailored to your specific stack. Without it, `cse` uses a built-in knowledge base covering 30+ AWS resource types.

```sh
export ANTHROPIC_API_KEY=sk-ant-...
cse --stack-name my-stack --template-file template.yaml
```

---

## Pipeline recipes

```sh
# Gate the pipeline — fail if anything would be replaced or deleted
cse --stack-name $STACK --template-file $TEMPLATE --no-interactive

# Warn only — show plan with warnings, never block (adoption ramp)
cse --stack-name $STACK --template-file $TEMPLATE --warn-only --execute --no-interactive

# Auto-deploy — show plan and execute regardless of what's changing
cse --stack-name $STACK --template-file $TEMPLATE --auto-approve --execute --no-interactive
```
