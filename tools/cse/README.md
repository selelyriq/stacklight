# cse — CloudFormation Change Set Explainer

CloudFormation change sets show you _what_ is changing but not _what that means_. `cse` creates the change set and rewrites the output in plain English — surfacing risk levels, calling out replacements, and flagging data-loss scenarios — so you actually understand what's about to happen before you hit deploy.

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

**Explain and auto-execute (no prompt):**
```sh
cse --stack-name my-stack --template-file template.yaml --execute --no-interactive
```

**Explain an existing change set (don't create a new one):**
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
| `--region` | AWS region override |
| `--profile` | AWS named profile |
| `--no-color` | Disable ANSI color output |
| `--no-ai` | Skip Claude API, use built-in explanations only |

---

## AI mode

When `ANTHROPIC_API_KEY` is set, `cse` sends the change set to Claude and gets back nuanced, context-aware explanations tailored to your specific stack. Without it, it falls back to a built-in knowledge base covering 30+ AWS resource types.

```sh
export ANTHROPIC_API_KEY=sk-ant-...
cse --stack-name my-stack --template-file template.yaml
```

---

## What the output looks like

```
╔══════════════════════════════════════════════════════════════╗
║   cse — CloudFormation Change Set Explainer                  ║
║   Stack:      my-production-stack                            ║
║   Change set: cse-1711234567-a3f2b1                          ║
║   Type:       UPDATE                                         ║
╚══════════════════════════════════════════════════════════════╝

  SUMMARY: 3 changes — 1 addition, 1 modification, 1 removal
  RISK:    HIGH — review replacements and removals carefully

  ────────────────────────────────────────────────────────────

  1. ADD     NewCacheCluster  [LOW]
     AWS::ElastiCache::CacheCluster
     New ElastiCache cluster will be created. No impact on existing resources.

  2. MODIFY  ApiService  [MEDIUM]
     AWS::ECS::Service → arn:aws:ecs:...
     ECS service updated. New task definition deployed via rolling update.
     Downtime unlikely if you have multiple tasks.
     Changed: TaskDefinition

  3. REMOVE  OldTable  [HIGH]
     AWS::DynamoDB::Table → my-production-stack-OldTable-XXXX
     DynamoDB table will be DELETED. ALL DATA WILL BE LOST.

  ────────────────────────────────────────────────────────────

  Execute this change set? [y/N]
```

---

## Pipeline usage

```sh
# CI/CD: show plan, auto-execute, exit non-zero on failure
cse --stack-name $STACK --template-file $TEMPLATE --no-interactive --execute
```
