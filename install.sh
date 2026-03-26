#!/usr/bin/env bash
# Stacklight installer
# Installs cse, drift, cfcat, and chromaform in one shot.
# Usage: ./install.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ZSHRC="$HOME/.zshrc"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { printf "${GREEN}  ✓${NC}  %s\n" "$1"; }
warn() { printf "${YELLOW}  ⚠${NC}  %s\n" "$1"; }
info() { printf "${CYAN}  →${NC}  %s\n" "$1"; }
fail() { printf "${RED}  ✗${NC}  %s\n" "$1" >&2; }

echo ""
printf "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${NC}\n"
printf "${BOLD}${CYAN}║          Stacklight Installer                ║${NC}\n"
printf "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${NC}\n"
echo ""

# ── Prerequisite checks ───────────────────────────────────────────────────────
echo "Checking prerequisites..."
echo ""

MISSING_REQUIRED=0

if command -v python3 &>/dev/null; then
    ok "python3 found ($(python3 --version 2>&1))"
else
    fail "python3 not found — install Python 3 and retry"
    MISSING_REQUIRED=1
fi

if command -v aws &>/dev/null; then
    ok "aws CLI found"
else
    warn "aws CLI not found — cse and drift require it (https://aws.amazon.com/cli/)"
fi

if command -v jq &>/dev/null; then
    ok "jq found"
else
    warn "jq not found — drift requires it (brew install jq)"
fi

if command -v code &>/dev/null; then
    ok "VSCode 'code' CLI found"
else
    warn "VSCode 'code' CLI not found — chromaform will be installed but may need manual VSCode restart"
fi

if [[ "$MISSING_REQUIRED" -ne 0 ]]; then
    echo ""
    fail "Required dependencies missing. Fix the above and re-run install.sh."
    exit 1
fi

echo ""
echo "Checking optional Python packages..."
echo ""

if python3 -c "import boto3" &>/dev/null 2>&1; then
    ok "boto3 installed"
else
    warn "boto3 not installed — cse requires it: pip3 install boto3"
fi

if python3 -c "import anthropic" &>/dev/null 2>&1; then
    ok "anthropic SDK installed (AI mode available for cse)"
else
    info "anthropic SDK not installed — optional, enables AI explanations in cse"
    info "Install with: pip3 install anthropic"
fi

if python3 -c "import yaml" &>/dev/null 2>&1; then
    ok "PyYAML installed"
else
    info "PyYAML not installed — optional, enables YAML support in cfcat"
    info "Install with: pip3 install pyyaml"
fi

echo ""
echo "Installing tools..."
echo ""

# ── Helper: add line to .zshrc idempotently ───────────────────────────────────
add_to_zshrc() {
    local marker="$1"
    local line="$2"
    if grep -qF "$marker" "$ZSHRC" 2>/dev/null; then
        ok "$marker already in $ZSHRC — skipping"
    else
        printf "\n%s\n%s\n" "$marker" "$line" >> "$ZSHRC"
        ok "Added to $ZSHRC: $line"
    fi
}

# ── 1. cse — CloudFormation Change Set Explainer ─────────────────────────────
echo "Installing cse..."
mkdir -p "$HOME/bin"
cp "$REPO_DIR/tools/cse/cse" "$HOME/bin/cse"
chmod +x "$HOME/bin/cse"
ok "Copied cse → ~/bin/cse"

add_to_zshrc \
    "# stacklight: ~/bin in PATH" \
    'export PATH="$HOME/bin:$PATH"'

# ── 2. drift — CloudFormation Drift Detector ─────────────────────────────────
echo ""
echo "Installing drift..."
cp "$REPO_DIR/tools/drift/drift.zsh" "$HOME/.drift.zsh"
ok "Copied drift.zsh → ~/.drift.zsh"

add_to_zshrc \
    "# stacklight: drift" \
    "source \"\$HOME/.drift.zsh\""

# ── 3. cfcat — CloudFormation Catalog ────────────────────────────────────────
echo ""
echo "Installing cfcat..."
chmod +x "$REPO_DIR/tools/cfcat/cfcat.py"

CFCAT_ALIAS="alias cfcat='python3 $REPO_DIR/tools/cfcat/cfcat.py'"
add_to_zshrc \
    "# stacklight: cfcat" \
    "$CFCAT_ALIAS"

# ── 4. chromaform — VSCode YAML syntax highlighter ───────────────────────────
echo ""
echo "Installing chromaform..."
CHROMAFORM_DEST="$HOME/.vscode/extensions/local.chromaform-0.0.1"
mkdir -p "$CHROMAFORM_DEST"
cp "$REPO_DIR/tools/chromaform/extension.js" "$CHROMAFORM_DEST/extension.js"
cp "$REPO_DIR/tools/chromaform/package.json" "$CHROMAFORM_DEST/package.json"
ok "Copied chromaform → $CHROMAFORM_DEST"

if command -v code &>/dev/null; then
    info "Reloading VSCode extensions list..."
    # VSCode auto-loads extensions from ~/.vscode/extensions on startup.
    # A window reload picks it up immediately.
    code --list-extensions &>/dev/null || true
    ok "chromaform will activate on next VSCode window reload (Cmd+Shift+P → Reload Window)"
else
    info "chromaform installed — restart VSCode to activate"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
printf "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}\n"
printf "${BOLD}${GREEN}║         Installation complete!               ║${NC}\n"
printf "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}\n"
echo ""
echo "Reload your shell to activate cse, drift, and cfcat:"
echo ""
printf "  ${CYAN}source ~/.zshrc${NC}\n"
echo ""
echo "Then try:"
printf "  ${CYAN}cse --help${NC}\n"
printf "  ${CYAN}drift my-stack-name${NC}\n"
printf "  ${CYAN}cfcat --version${NC}\n"
echo ""
echo "Reload your VSCode window to activate chromaform:"
printf "  ${CYAN}Cmd+Shift+P → Reload Window${NC}\n"
echo ""
