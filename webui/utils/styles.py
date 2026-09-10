"""
Trading Agents Framework - CSS Styles
"""

# CSS for better styling
from webui.config.tokens import css_variables

_CSS_TEMPLATE = """
/* Design tokens. Defined once in webui/config/tokens.py and emitted here,
   so Python-side styling and the stylesheet cannot disagree. */
__TOKENS__


.gradio-container {
    max-width: 100% !important;
    padding: 0 !important;
}
.main-container {
    margin: 0;
    padding: 0;
}
.report-box {
    height: 500px;
    overflow-y: auto;
    border: 1px solid #ddd;
    border-radius: 4px;
    padding: 10px;
    background-color: #f9f9f9;
}
.status-table {
    border-collapse: collapse;
    width: 100%;
    font-size: 14px;
    margin-bottom: 15px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
.status-table td, .status-table th {
    border: 1px solid #ccc;
    padding: 10px;
    transition: background-color 0.5s ease;
}
.status-table tr:nth-child(even) {
    background-color: var(--ta-surface-inset);
}
.status-table tr:nth-child(odd) {
    background-color: var(--ta-surface);
}
/* Ensure text is visible on all rows regardless of background */
.status-table td {
    color: #333;
    font-weight: 500;
}
.status-table th {
    padding-top: 12px;
    padding-bottom: 12px;
    text-align: left;
    background-color: #2C3E50;
    color: white;
    font-weight: bold;
}
.pending {
    color: #7F8C8D !important;
    font-weight: bold;
}
.in-progress {
    color: #2980B9 !important;
    font-weight: bold;
    animation: pulse-blue 2s infinite;
}
.completed {
    color: #27AE60 !important;
    font-weight: bold;
    animation: flash-green 1s 1;
}
@keyframes pulse-blue {
    0% { opacity: 0.7; }
    50% { opacity: 1; }
    100% { opacity: 0.7; }
}
@keyframes flash-green {
    0% { background-color: rgba(39, 174, 96, 0.3); }
    100% { background-color: transparent; }
}
.tabs {
    margin-top: 20px;
}
.time-period-btn {
    margin: 5px;
    padding: 8px 16px !important;
    border-radius: 4px;
    font-weight: bold !important;
    border: 1px solid #ddd !important;
    background-color: #f8f9fa !important;
    color: #333 !important;
}
.time-period-btn.active {
    background-color: #2C3E50 !important;
    color: white !important;
    border-color: #2C3E50 !important;
}
.chart-controls {
    padding: 10px;
    background-color: #f9f9f9;
    border-radius: 4px;
    margin-bottom: 10px;
    border: 1px solid #ddd;
    display: flex;
    justify-content: center;
}
.chart-controls-heading {
    margin: 0;
    padding: 10px;
    background-color: #2C3E50;
    color: white;
    border-radius: 4px 4px 0 0;
    font-weight: bold;
    font-size: 16px;
}
.stats-container {
    margin-top: 15px;
    padding: 12px;
    background-color: #2C3E50;
    border-radius: 5px;
    font-size: 15px;
    color: white !important;
    font-weight: bold;
    text-align: center;
}
.auto-refresh-indicator {
    display: inline-block;
    margin-left: 10px;
    padding: 3px 8px;
    background-color: #27AE60;
    border-radius: 3px;
    font-size: var(--ta-size-small);
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0% { opacity: 0.7; }
    50% { opacity: 1; }
    100% { opacity: 0.7; }
}

/* --- Decision workbench ------------------------------------------------ */
.workbench-panel {
    background-color: var(--ta-surface);
    border: 1px solid var(--ta-border);
    border-radius: var(--ta-radius-lg);
    padding: var(--ta-space-md);
    margin-bottom: var(--ta-space-md);
}
.workbench-rail {
    display: flex;
    align-items: center;
    padding: 8px 4px;
}
.workbench-rail-node {
    display: flex;
    flex-direction: column;
    align-items: center;
    min-width: 64px;
}
.workbench-rail-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    transition: box-shadow 0.2s ease;
}
.workbench-rail-label {
    font-size: var(--ta-size-small);
    color: var(--ta-text-muted);
    margin-top: 6px;
    white-space: nowrap;
}
.workbench-rail-link {
    flex: 1;
    height: 2px;
    background-color: var(--ta-border);
    margin: 0 4px 18px 4px;
}
.workbench-stage-card {
    background-color: var(--ta-background);
    border: 1px solid var(--ta-border);
}
.workbench-stage-name {
    font-weight: var(--ta-weight-medium);
    color: var(--ta-text);
}
.workbench-details {
    border: 1px solid var(--ta-border);
    border-radius: var(--ta-radius);
    margin-bottom: 8px;
}
.workbench-summary {
    cursor: pointer;
    padding: 6px 10px;
    color: var(--ta-text);
    font-size: var(--ta-size-body);
}
.workbench-dropdown .Select-control,
.workbench-dropdown .Select-menu-outer {
    background-color: var(--ta-background);
    color: var(--ta-text);
}

/* --- Vitals strip ------------------------------------------------------ */
.vitals-strip {
    display: flex;
    flex-wrap: wrap;
    gap: 1px;
    background-color: var(--ta-border);
    border: 1px solid var(--ta-border);
    border-radius: var(--ta-radius-lg);
    overflow: hidden;
}
.vitals-cell {
    flex: 1 1 140px;
    min-width: 140px;
    background-color: var(--ta-surface);
    padding: 10px 14px;
}
.vitals-label {
    font-size: var(--ta-size-micro);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ta-text-faint);
}
.vitals-value {
    font-size: var(--ta-size-heading);
    font-weight: var(--ta-weight-medium);
    line-height: 1.3;
}
.vitals-hint {
    font-size: var(--ta-size-small);
    color: var(--ta-text-faint);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* --- Pipeline board ---------------------------------------------------- */
.board-columns {
    display: flex;
    gap: var(--ta-space-sm);
    overflow-x: auto;
    padding-bottom: 4px;
}
.board-column {
    flex: 1 1 0;
    min-width: 150px;
    background-color: var(--ta-background);
    border: 1px solid var(--ta-border);
    border-radius: var(--ta-radius);
}
.board-column-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 10px;
    border-bottom: 1px solid var(--ta-border);
}
.board-column-title {
    font-size: var(--ta-size-small);
    font-weight: var(--ta-weight-medium);
    color: var(--ta-text);
}
.board-column-count {
    font-size: var(--ta-size-small);
    color: var(--ta-text-faint);
    background-color: var(--ta-surface);
    border-radius: 10px;
    padding: 0 7px;
}
.board-column-body {
    padding: var(--ta-space-sm);
    display: flex;
    flex-direction: column;
    gap: var(--ta-space-sm);
    min-height: 60px;
    max-height: 320px;
    overflow-y: auto;
}
.board-column-empty {
    color: var(--ta-border);
    text-align: center;
    font-size: var(--ta-size-small);
    padding: 8px 0;
}
.board-card {
    background-color: var(--ta-surface);
    border: 1px solid var(--ta-border);
    border-left: 3px solid var(--ta-text-faint);
    border-radius: 4px;
    padding: 7px 9px;
    cursor: pointer;
    transition: border-color 0.15s ease, transform 0.15s ease;
}
.board-card:hover {
    border-color: var(--ta-accent);
    transform: translateY(-1px);
}
.board-card-symbol {
    font-weight: var(--ta-weight-medium);
    font-size: var(--ta-size-body);
    color: var(--ta-text);
}
.board-card-badge {
    font-size: 9px;
}
.board-card-headline {
    font-size: var(--ta-size-small);
    color: var(--ta-text-muted);
    margin-top: 3px;
    line-height: 1.35;
}
.board-card-hint {
    font-size: var(--ta-size-micro);
    color: var(--ta-text-faint);
    margin-top: 2px;
}
.board-card-active {
    border-color: var(--ta-accent);
    background-color: var(--ta-surface-raised);
    box-shadow: inset 0 0 0 1px var(--ta-accent);
}
.board-card-live {
    cursor: default;
}
.board-card-live:hover {
    border-color: var(--ta-border);
    transform: none;
}

/* ── Stages ──────────────────────────────────────────────────────────────
   The five tabs, and the rules that stop a panel widening the page.

   `min-width: 0` is the one that matters. A flex or grid child defaults to
   `min-width: auto`, which resolves to "as wide as my widest content", so a
   single long table or an unconstrained Plotly figure would widen its
   column, then its row, then the document. Zero lets the column win and
   the overflow scroll inside the panel where it belongs. */

.app-shell {
    max-width: 1800px;
}

.stage-tabs {
    border-bottom: 1px solid var(--ta-border);
    margin-bottom: var(--ta-space-lg);
}

.stage-tab .nav-link,
.stage-tabs .nav-link {
    color: var(--ta-text-muted);
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    padding: var(--ta-space-md) var(--ta-space-lg);
    font-weight: 600;
    letter-spacing: 0.01em;
}

.stage-tabs .nav-link:hover {
    color: var(--ta-text);
    border-bottom-color: var(--ta-border);
}

.stage-tabs .nav-link.active {
    color: var(--ta-text);
    background: transparent;
    border-bottom-color: var(--ta-accent);
}

.stage-blurb {
    color: var(--ta-text-muted);
    font-size: 0.9rem;
    margin: var(--ta-space-md) 0 var(--ta-space-lg);
    max-width: 68ch;
}

.stage-body {
    display: flex;
    flex-direction: column;
    min-width: 0;
}

.panel-shell {
    min-width: 0;
    max-width: 100%;
}

.panel-shell-body {
    min-width: 0;
    max-width: 100%;
}

/* Wide things scroll in their own box rather than stretching the page.
   Tables and pre blocks are the usual offenders; a figure is handled by
   Plotly's own responsive sizing. */
.panel-shell-body .table-responsive,
.panel-shell-body table,
.panel-shell-body pre {
    max-width: 100%;
    overflow-x: auto;
}

.panel-shell-body pre {
    white-space: pre-wrap;
    word-break: break-word;
}

/* Bootstrap columns inherit the same default, so they need it too. */
.panel-shell-body .row > [class^="col"],
.panel-shell-body .row > [class*=" col"] {
    min-width: 0;
}

.app-footer {
    padding-top: var(--ta-space-lg);
    border-top: 1px solid var(--ta-border);
}

/* ── Set up ──────────────────────────────────────────────────────────── */

.setup-requirement {
    padding: var(--ta-space-md) 0;
    border-bottom: 1px solid var(--ta-border);
}

.setup-requirement:last-child {
    border-bottom: none;
}
"""

# .replace rather than %-formatting: the stylesheet is full of literal
# percent signs (keyframes, widths) that would break interpolation.
CSS = _CSS_TEMPLATE.replace("__TOKENS__", css_variables())

# AUTO_REFRESH_JS used to live here: a polling loop that stamped an
# "Updated:" badge onto the page. Nothing has imported it since the
# server-sent pulse replaced polling, and it carried the last colour in
# this file that no theme could reach. Deleted rather than tokenised —
# there is no sense theming a string nobody runs.
