# chromaform — YAML Syntax Highlighter for VSCode

Plain YAML is hard to scan. Every key is the same color, resource types blend into property values, and `!Ref` looks identical to everything else. ChromaForm adds semantic syntax highlighting to YAML files in VSCode — coloring keys by indentation depth and highlighting file-type-specific constructs like CloudFormation resource types, Kubernetes kinds, and GitHub Actions expressions.

---

## What it highlights

### All YAML files
| Element | Color |
|---------|-------|
| Top-level section keys (`Resources:`, `Parameters:`) | Red/bold |
| Second-level keys (resource IDs, job names) | Orange/bold |
| Property keys | Light blue |
| Regular values | Light blue (lighter) |
| Booleans (`true`, `false`, `yes`, `no`) | White |
| Structural characters (`{}[]"$>*\|&`) | Red |

### CloudFormation (auto-detected via `AWSTemplateFormatVersion`)
| Element | Color |
|---------|-------|
| AWS resource types (`AWS::EC2::VPC`) | Purple |
| Intrinsic functions (`!Ref`, `!GetAtt`, `!Sub`, `!If`) | Orange |
| `!Ref` and `!GetAtt` targets | Amber |
| `${VarName}` in `!Sub` strings | Amber |
| Parameter and Output names | Amber/bold |

### Kubernetes (auto-detected via `apiVersion` + `kind`)
| Element | Color |
|---------|-------|
| `kind` and `apiVersion` values | Purple |
| `image` values (container references) | Amber |

### GitHub Actions (auto-detected via `jobs:` / `on:`)
| Element | Color |
|---------|-------|
| `uses:` values (external action references) | Orange |
| `runs-on:` values (execution environment) | Purple |
| `${{ expression }}` contents | Amber |

---

## Installation

### Via Stacklight installer (recommended)
```sh
./install.sh
```

### Manual install
```sh
# Copy the extension directory into VSCode's extensions folder
cp -r tools/chromaform ~/.vscode/extensions/local.chromaform-0.0.1

# Reload VSCode
# Cmd+Shift+P → Reload Window
```

---

## How flavor detection works

ChromaForm scans the first 30 lines of each YAML file to detect its type:

1. Contains `AWSTemplateFormatVersion` → CloudFormation mode
2. Contains both `apiVersion:` and `kind:` → Kubernetes mode
3. Contains `jobs:` or `on:` with workflow triggers → GitHub Actions mode
4. Otherwise → generic YAML mode

Detection is automatic — no configuration or file naming conventions required.

---

## Requirements

- VSCode `^1.95.0`
- No extensions from the marketplace required
- Works with any `.yaml` or `.yml` file (language ID: `yaml`)
