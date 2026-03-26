# Stacklight

A suite of developer tools for AWS CloudFormation workflows. Stacklight makes deploying, auditing, and understanding infrastructure-as-code faster and more readable.

---

## Tools

| Tool | What it does | Language |
|------|-------------|----------|
| [**cse**](tools/cse/) | Translates CloudFormation change sets into plain English before you deploy | Python |
| [**drift**](tools/drift/) | Detects stack drift and generates a structured remediation report | Zsh |
| [**cfcat**](tools/cfcat/) | Concatenates CloudFormation templates into an LLM-ready summary + interactive diagram | Python |
| [**chromaform**](tools/chromaform/) | VSCode extension that adds syntax highlighting to CloudFormation, Kubernetes, and GitHub Actions YAML | JavaScript |

---

## Quick Install

**One command — installs all four tools:**

```sh
git clone https://github.com/YOUR_USERNAME/stacklight.git
cd stacklight
./install.sh
source ~/.zshrc
```

Then reload your VSCode window (`Cmd+Shift+P` → `Reload Window`) to activate chromaform.

---

## Prerequisites

| Dependency | Required by | Install |
|-----------|------------|---------|
| Python 3 | cse, cfcat | [python.org](https://www.python.org/downloads/) |
| AWS CLI | cse, drift | `brew install awscli` |
| jq | drift | `brew install jq` |
| VSCode | chromaform | [code.visualstudio.com](https://code.visualstudio.com/) |
| boto3 | cse | `pip3 install boto3` |
| PyYAML | cfcat (optional) | `pip3 install pyyaml` |
| anthropic | cse (optional) | `pip3 install anthropic` |

---

## Tools at a Glance

### cse — Change Set Explainer

CloudFormation change sets are hard to read. `cse` creates a change set and rewrites the output in plain English, calling out risk levels, replacements, and data-loss scenarios before you deploy.

```sh
cse --stack-name my-stack --template-file template.yaml
cse --stack-name my-stack --template-file template.yaml --execute
```

Set `ANTHROPIC_API_KEY` to enable AI-powered explanations via Claude.

→ [Full docs](tools/cse/README.md)

---

### drift — Drift Detector

`drift` runs CloudFormation drift detection and turns the raw API output into a clean terminal summary plus an optional markdown report with a copy-paste LLM context block for remediation help.

```sh
drift my-stack-name
drift my-stack-name --out
drift my-stack-name --region us-west-2 --profile staging
```

→ [Full docs](tools/drift/README.md)

---

### cfcat — CloudFormation Catalog

`cfcat` reads one or more CloudFormation templates (or entire directories) and produces a structured, LLM-friendly summary of your infrastructure — including cross-stack dependencies, resource wiring, and deployment order. Can also generate an interactive HTML diagram.

```sh
cfcat template.yaml
cfcat ./infra/ -o summary.txt
cfcat ./infra/ --html
cfcat ./infra/ --docs
```

→ [Full docs](tools/cfcat/README.md)

---

### chromaform — YAML Syntax Highlighter

A VSCode extension that colors YAML keys by indentation depth and adds semantic highlighting for CloudFormation resource types, intrinsic functions, Kubernetes kinds, and GitHub Actions expressions. Makes dense YAML files significantly easier to scan.

No configuration needed — it activates automatically on any `.yaml` file.

→ [Full docs](tools/chromaform/README.md)

---

## Configuration

### AI mode for cse

Set your Anthropic API key to enable Claude-powered explanations:

```sh
export ANTHROPIC_API_KEY=sk-ant-...
```

Add it to `~/.zshrc` to make it permanent. Without it, `cse` falls back to its built-in knowledge base (still very useful).

### AWS credentials

All tools that talk to AWS (`cse`, `drift`) use standard boto3/AWS CLI credential resolution — environment variables, `~/.aws/credentials`, or instance profiles. Pass `--profile` and `--region` to override per-command.

---

## What "stacklight" means

A stack light (or andon light) is the industrial signal tower on a factory floor that tells workers at a glance whether a machine is running, warning, or faulted. These tools are the signal lights for your CloudFormation stacks — they surface what's happening, what's changed, and what needs attention, without digging through the AWS console.
