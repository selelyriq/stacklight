# cfcat — CloudFormation Catalog

CloudFormation templates are dense. Reading through hundreds of lines of YAML to understand what a stack does, what it depends on, and how resources connect is tedious — especially when you're handing a template to an AI for analysis. `cfcat` parses your templates and produces a structured, LLM-friendly summary with resource wiring, cross-stack dependencies, deployment order, and optionally an interactive HTML diagram.

---

## Prerequisites

```sh
pip3 install pyyaml     # optional — required for YAML templates; JSON works without it
```

No AWS credentials needed — `cfcat` processes local files only.

---

## Usage

**Summarize a single template:**
```sh
cfcat template.yaml
```

**Scan an entire directory:**
```sh
cfcat ./infra/
```

**Multiple paths, custom output file:**
```sh
cfcat ./infra/ ./shared/ -o my-infra.txt
```

**Generate an interactive HTML wiring diagram:**
```sh
cfcat ./infra/ --html
# → cfcat-output.html
```

**Generate Markdown documentation with AI context block:**
```sh
cfcat ./infra/ --docs
# → cfcat-output.md
```

**Verbose (print progress to stdout):**
```sh
cfcat ./infra/ --verbose
```

---

## Flags

| Flag | Description |
|------|-------------|
| `<FILE_OR_DIR>` | CloudFormation file(s) or directories to scan (required) |
| `-o, --output FILE` | Output file path (default: `cfcat-output.txt`) |
| `--no-summary` | Skip the global infrastructure summary section |
| `--verbose` | Print progress to stdout |
| `--html` | Generate interactive wiring diagram (.html) |
| `--docs` | Generate Markdown documentation with AI context block (.md) |
| `--version` | Print version |

---

## Output sections

### Per template
- Description and template version
- Parameters (types, descriptions, defaults, allowed values)
- Conditions
- Resources with key properties, conditions, deletion policies, and dependencies
- Wiring — intra-template resource connections (via Ref/GetAtt/DependsOn)
- Outputs and exports

### Global summary (multi-template)
- Total resource counts by type
- Cross-stack imports and exports
- Nested stack references
- Dependency map with deployment order
- Detected infrastructure patterns (VPC, ECS, RDS, Lambda, etc.)

---

## Interactive HTML diagram

`--html` generates a force-directed SVG diagram where each resource is a node:

- **Click** a node to see its properties in a detail panel
- **Hover** an edge to see the reference type (Ref, GetAtt, DependsOn)
- Nodes are color-coded by category (networking, compute, database, etc.)
- Resources within the same template are grouped

---

## LLM workflow

The default text output and `--docs` markdown are designed to be pasted directly into Claude or another LLM:

```sh
cfcat ./infra/ -o context.txt
# paste context.txt into Claude:
# "Here's my CloudFormation infrastructure. What security issues do you see?"
# "Draw a diagram of how these stacks depend on each other."
# "What would break if I deleted the VPC stack?"
```
