const vscode = require('vscode');

// ── Base regexes (all flavors) ─────────────────────────────────────────────
const KEY_RE     = /^(\s*)([a-zA-Z0-9_][a-zA-Z0-9_\-.]*)(\s*:)/;
const KV_RE      = /^(\s*[a-zA-Z0-9_][a-zA-Z0-9_\-.]*\s*:\s*)(\S.*)/;
const LIST_RE    = /^(\s*-\s+)(\S.*)/;
const BOOL_RE    = /\b(true|false|yes|no|True|False|Yes|No|TRUE|FALSE|YES|NO)\b/g;
const SPECIAL_RE = /[(){}[\]"$>*|&]/g;

// ── CFN-specific regexes ───────────────────────────────────────────────────
const CFN_FN_RE         = /!\w+/g;
const CFN_TYPE_RE       = /\b[A-Z][a-zA-Z0-9]*::[A-Z][a-zA-Z0-9]*::[A-Za-z0-9:.<>]+/g;
const CFN_REF_TARGET_RE = /!(Ref|GetAtt)\s+(\S+)/g;
const CFN_SUB_VAR_RE    = /\$\{([^}]+)\}/g;

// ── GHA-specific regexes ───────────────────────────────────────────────────
const GHA_EXPR_RE = /\$\{\{(.*?)\}\}/g;

// ── Flavor detection (scans first 30 lines) ────────────────────────────────
function detectFlavor(doc) {
    const lineCount = Math.min(doc.lineCount, 30);
    const lines = [];
    for (let i = 0; i < lineCount; i++) lines.push(doc.lineAt(i).text);
    const text = lines.join('\n');

    if (/\bAWSTemplateFormatVersion\b/.test(text)) return 'cfn';

    if (/^apiVersion:\s*\S/m.test(text) && /^kind:\s*\S/m.test(text)) return 'k8s';

    if (/^jobs:/m.test(text) ||
        (/^on:/m.test(text) &&
         /^\s+(push|pull_request|workflow_dispatch|schedule|release|workflow_call):/m.test(text)))
        return 'gha';

    return 'generic';
}

// ── Tier-1 sections whose keys get amber instead of orange ─────────────────
const PARAM_SECTIONS = {
    cfn:     new Set(['Parameters', 'Outputs']),
    gha:     new Set(['jobs']),
    k8s:     new Set(),
    generic: new Set(),
};

function tierFor(indent) {
    if (indent === 0) return 0;
    if (indent <= 2)  return 1;
    return 2;
}

function activate(context) {
    const tiers = [
        vscode.window.createTextEditorDecorationType({ color: '#ff7b72', fontStyle: 'bold' }), // tier 0: top-level sections
        vscode.window.createTextEditorDecorationType({ color: '#ffa657', fontStyle: 'bold' }), // tier 1: resource/job IDs
        vscode.window.createTextEditorDecorationType({ color: '#79c0ff' }),                    // tier 2: property keys
    ];

    // mark 5 — amber: param/output/job names + !Ref targets + ${ } contents + k8s images
    // mark 4 — purple: AWS::*::* types + k8s kind/apiVersion + GHA runs-on
    // mark 3 — orange: CFN !functions + GHA uses:
    // mark 2 — red: structural special chars
    // mark 1 — white: booleans
    // mark 0 — light blue: regular values
    const paramKeyColor = vscode.window.createTextEditorDecorationType({ color: '#f0c674', fontStyle: 'bold' });
    const valueColor    = vscode.window.createTextEditorDecorationType({ color: '#a5d6ff' });
    const boolColor     = vscode.window.createTextEditorDecorationType({ color: '#FFFFFF' });
    const specialColor  = vscode.window.createTextEditorDecorationType({ color: '#ff7b72' });
    const fnColor       = vscode.window.createTextEditorDecorationType({ color: '#ffa657' });
    const typeColor     = vscode.window.createTextEditorDecorationType({ color: '#d2a8ff' });
    const refColor      = vscode.window.createTextEditorDecorationType({ color: '#f0c674' });

    function update(editor) {
        if (!editor || editor.document.languageId !== 'yaml') return;

        const doc           = editor.document;
        const flavor        = detectFlavor(doc);
        const paramSections = PARAM_SECTIONS[flavor];

        const keyRanges      = [[], [], []];
        const paramKeyRanges = [];
        const valueRanges    = [];
        const boolRanges     = [];
        const specialRanges  = [];
        const fnRanges       = [];
        const typeRanges     = [];
        const refRanges      = [];

        let currentSection = null;

        for (let i = 0; i < doc.lineCount; i++) {
            const text    = doc.lineAt(i).text;
            const km      = KEY_RE.exec(text);
            let   keyName = null;

            // ── Key coloring + section tracking ───────────────────────────
            if (km) {
                const indent = km[1].length;
                const tier   = tierFor(indent);
                const range  = new vscode.Range(i, indent, i, indent + km[2].length);
                keyName = km[2];

                if (indent === 0) {
                    currentSection = km[2];
                    keyRanges[0].push(range);
                } else if (tier === 1 && paramSections.has(currentSection)) {
                    paramKeyRanges.push(range); // amber: parameter/output/job names
                } else {
                    keyRanges[tier].push(range);
                }
            }

            // ── Value coloring ─────────────────────────────────────────────
            let valueStart = -1;
            const kvMatch = KV_RE.exec(text);
            if (kvMatch) {
                valueStart = kvMatch[1].length;
            } else {
                const listMatch = LIST_RE.exec(text);
                if (listMatch) valueStart = listMatch[1].length;
            }

            if (valueStart < 0) continue;

            const valueEnd  = text.trimEnd().length;
            const valueText = text.slice(valueStart, valueEnd);
            const len       = valueText.length;

            // Mark array — higher number = higher priority, last write wins
            const marks = new Uint8Array(len);

            // Base marks (all flavors)
            BOOL_RE.lastIndex = 0;
            let bm;
            while ((bm = BOOL_RE.exec(valueText)) !== null) {
                for (let k = bm.index; k < bm.index + bm[0].length; k++) marks[k] = 1;
            }

            SPECIAL_RE.lastIndex = 0;
            let sm;
            while ((sm = SPECIAL_RE.exec(valueText)) !== null) marks[sm.index] = 2;

            // Flavor-specific marks
            if (flavor === 'cfn') {
                CFN_FN_RE.lastIndex = 0;
                let fm;
                while ((fm = CFN_FN_RE.exec(valueText)) !== null) {
                    for (let k = fm.index; k < fm.index + fm[0].length; k++) marks[k] = 3;
                }

                CFN_TYPE_RE.lastIndex = 0;
                let tm;
                while ((tm = CFN_TYPE_RE.exec(valueText)) !== null) {
                    for (let k = tm.index; k < tm.index + tm[0].length; k++) marks[k] = 4;
                }

                CFN_REF_TARGET_RE.lastIndex = 0;
                let rm;
                while ((rm = CFN_REF_TARGET_RE.exec(valueText)) !== null) {
                    const start = rm.index + rm[0].length - rm[2].length;
                    for (let k = start; k < start + rm[2].length; k++) marks[k] = 5;
                }

                CFN_SUB_VAR_RE.lastIndex = 0;
                let sv;
                while ((sv = CFN_SUB_VAR_RE.exec(valueText)) !== null) {
                    const start = sv.index + 2; // skip "${"
                    for (let k = start; k < start + sv[1].length; k++) marks[k] = 5;
                }

            } else if (flavor === 'k8s') {
                // kind/apiVersion values → purple (the resource type identifier)
                if (keyName === 'kind' || keyName === 'apiVersion') {
                    for (let k = 0; k < len; k++) marks[k] = 4;
                }
                // image: values → amber (references to external container images)
                if (keyName === 'image') {
                    for (let k = 0; k < len; k++) marks[k] = 5;
                }

            } else if (flavor === 'gha') {
                // uses: values → orange (invoking an external action)
                if (keyName === 'uses') {
                    for (let k = 0; k < len; k++) marks[k] = 3;
                }
                // runs-on: values → purple (defines the execution environment)
                if (keyName === 'runs-on') {
                    for (let k = 0; k < len; k++) marks[k] = 4;
                }
                // ${{ expression }} → content is amber, delimiters stay red
                GHA_EXPR_RE.lastIndex = 0;
                let ge;
                while ((ge = GHA_EXPR_RE.exec(valueText)) !== null) {
                    const start = ge.index + 3; // skip "${{" (3 chars)
                    for (let k = start; k < start + ge[1].length; k++) marks[k] = 5;
                }
            }

            // Run-length encode marks into non-overlapping ranges
            let runStart = 0;
            for (let k = 1; k <= len; k++) {
                if (k === len || marks[k] !== marks[k - 1]) {
                    const r    = new vscode.Range(i, valueStart + runStart, i, valueStart + k);
                    const mark = marks[k - 1];
                    if      (mark === 0) valueRanges.push(r);
                    else if (mark === 1) boolRanges.push(r);
                    else if (mark === 2) specialRanges.push(r);
                    else if (mark === 3) fnRanges.push(r);
                    else if (mark === 4) typeRanges.push(r);
                    else                 refRanges.push(r);
                    runStart = k;
                }
            }
        }

        tiers.forEach((dec, j) => editor.setDecorations(dec, keyRanges[j]));
        editor.setDecorations(paramKeyColor, paramKeyRanges);
        editor.setDecorations(valueColor,    valueRanges);
        editor.setDecorations(boolColor,     boolRanges);
        editor.setDecorations(specialColor,  specialRanges);
        editor.setDecorations(fnColor,       fnRanges);
        editor.setDecorations(typeColor,     typeRanges);
        editor.setDecorations(refColor,      refRanges);
    }

    vscode.window.onDidChangeActiveTextEditor(update, null, context.subscriptions);
    vscode.workspace.onDidChangeTextDocument(e => {
        const ed = vscode.window.activeTextEditor;
        if (ed && e.document === ed.document) update(ed);
    }, null, context.subscriptions);

    update(vscode.window.activeTextEditor);
}

function deactivate() {}

module.exports = { activate, deactivate };
