/**
 * ResPlan Co-Design Studio — Official Export & Reporting Engine (exporter.js)
 *
 * Implements:
 * - One-click download of certified official competition JSON ({plan_id}_solution.json).
 * - One-click download of Technical Summary Reports ({plan_id}_report.md and .html).
 * - Vector SVG and raster PNG high-resolution schematic image downloads.
 * - Interactive modal inspection dialog with colorized JSON syntax highlighting and copy-to-clipboard.
 * - Algorithm comparison summary export (CSV / JSON format).
 * - Strict certification guardrails preventing uncertified exports from being labeled as valid.
 */

(function (root, factory) {
  'use strict';
  if (typeof define === 'function' && define.amd) {
    define([], factory);
  } else if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ResPlanExporter = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // --- Configuration ---
  const EXPORT_CONFIG = {
    endpoints: {
      certifiedJson: '/api/export/certified/json',
      fallbackJson: '/api/export/json',
      reportMd: '/api/export/report/markdown',
      reportHtml: '/api/export/report/html',
      schematicSvg: '/api/export/schematic/svg',
      overlayPng: '/api/export/png',
      comparisonCsv: '/api/export/comparison/csv',
      comparisonJson: '/api/export/comparison/json',
      oracleAudit: '/api/export/oracle/audit',
    },
    toastDurationMs: 3200,
  };

  // --- Utility Helpers ---

  /**
   * Trigger download of a Blob object in the browser.
   */
  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }, 150);
  }

  /**
   * Trigger download of arbitrary text with a given MIME type.
   */
  function downloadText(text, filename, mimeType = 'text/plain;charset=utf-8') {
    const blob = new Blob([text], { type: mimeType });
    downloadBlob(blob, filename);
  }

  /**
   * Display floating toast notification.
   */
  function showToast(message, type = 'info') {
    let container = document.getElementById('resplanToastContainer');
    if (!container) {
      container = document.createElement('div');
      container.id = 'resplanToastContainer';
      container.className = 'resplan-toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `resplan-toast toast-${type}`;
    const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warning' ? '⚠' : 'ℹ';
    toast.innerHTML = `<span class="toast-icon">${icon}</span><span class="toast-msg">${escapeHtml(message)}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 300);
    }, EXPORT_CONFIG.toastDurationMs);
  }

  /**
   * Basic HTML entity escaping.
   */
  function escapeHtml(str) {
    if (typeof str !== 'string') return String(str);
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  /**
   * Syntax highlight a JSON string into styled HTML spans.
   */
  function syntaxHighlightJson(jsonObj) {
    const jsonStr = typeof jsonObj === 'string' ? jsonObj : JSON.stringify(jsonObj, null, 2);
    return jsonStr.replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?|[\[\]{},])/g,
      function (match) {
        let cls = 'json-number';
        if (/^"/.test(match)) {
          if (/:$/.test(match)) {
            cls = 'json-key';
          } else {
            cls = 'json-string';
          }
        } else if (/true|false/.test(match)) {
          cls = 'json-boolean';
        } else if (/null/.test(match)) {
          cls = 'json-null';
        } else if (/^[\[\]{},]$/.test(match)) {
          cls = 'json-punct';
        }
        return `<span class="${cls}">${escapeHtml(match)}</span>`;
      }
    );
  }

  // --- Modal Dialog Component ---

  let modalElement = null;
  let currentModalData = null;

  function ensureModal() {
    if (modalElement) return modalElement;

    modalElement = document.createElement('div');
    modalElement.id = 'resplanExportModal';
    modalElement.className = 'resplan-modal-backdrop';
    modalElement.style.display = 'none';

    modalElement.innerHTML = `
      <div class="resplan-modal-dialog" role="dialog" aria-labelledby="modalTitle" aria-modal="true">
        <header class="modal-header">
          <div class="modal-title-group">
            <h3 id="modalTitle">Slide 8 Solution JSON Inspector</h3>
            <span id="modalPlanBadge" class="modal-badge-pill">Plan: --</span>
            <span id="modalCertPill" class="modal-cert-pill cert-valid">CERTIFIED</span>
          </div>
          <button id="modalBtnClose" class="modal-close-btn" title="Close inspector (Esc)">&times;</button>
        </header>

        <div class="modal-toolbar">
          <div class="modal-toolbar-left">
            <button id="modalBtnCopy" class="modal-tool-btn primary" title="Copy formatted JSON to clipboard">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>
                <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>
              </svg>
              <span>Copy JSON</span>
            </button>
            <button id="modalBtnDownloadJson" class="modal-tool-btn" title="Download solution JSON file">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
              </svg>
              <span>Download .json</span>
            </button>
            <button id="modalBtnDownloadMd" class="modal-tool-btn" title="Download Markdown Technical Report">
              <span>Report (.md)</span>
            </button>
            <button id="modalBtnDownloadHtml" class="modal-tool-btn" title="Download Printable HTML Report">
              <span>Report (.html)</span>
            </button>
            <button id="modalBtnDownloadSvg" class="modal-tool-btn" title="Download Vector SVG Schematic">
              <span>Schematic (.svg)</span>
            </button>
          </div>
          <div class="modal-toolbar-right">
            <input type="text" id="modalSearchInput" class="modal-search-input" placeholder="Filter keys..." aria-label="Filter JSON">
          </div>
        </div>

        <div class="modal-body">
          <pre class="json-code-view"><code id="modalJsonCode"></code></pre>
        </div>

        <footer class="modal-footer">
          <div class="modal-metrics-chips" id="modalMetricsChips">
            <span class="chip">Hubs: <strong id="mChipH">--</strong></span>
            <span class="chip">Length: <strong id="mChipL">--</strong> m</span>
            <span class="chip">Cost J: <strong id="mChipJ">--</strong></span>
            <span class="chip">Bends: <strong id="mChipB">--</strong></span>
            <span class="chip">Crossings: <strong id="mChipX">--</strong></span>
            <span class="chip">Area Gap: <strong id="mChipA">--</strong> m²</span>
          </div>
          <div class="modal-footer-actions">
            <button id="modalBtnDismiss" class="modal-dismiss-btn">Dismiss</button>
          </div>
        </footer>
      </div>
    `;

    document.body.appendChild(modalElement);
    injectModalStyles();
    attachModalEvents();

    return modalElement;
  }

  function injectModalStyles() {
    if (document.getElementById('resplanExporterStyles')) return;

    const style = document.createElement('style');
    style.id = 'resplanExporterStyles';
    style.textContent = `
      .resplan-modal-backdrop {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.75);
        backdrop-filter: blur(4px);
        z-index: 9999;
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 20px;
        animation: fadeIn 0.15s ease-out;
      }
      .resplan-modal-dialog {
        background: #0f172a;
        color: #f8fafc;
        border: 1px solid #334155;
        border-radius: 12px;
        width: 100%;
        max-width: 960px;
        max-height: 90vh;
        display: flex;
        flex-direction: column;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
        overflow: hidden;
      }
      .modal-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 16px 20px;
        border-bottom: 1px solid #1e293b;
        background: #1e293b;
      }
      .modal-title-group {
        display: flex;
        align-items: center;
        gap: 12px;
      }
      .modal-title-group h3 {
        margin: 0;
        font-size: 16px;
        font-weight: 700;
        color: #f8fafc;
      }
      .modal-badge-pill {
        font-size: 11px;
        font-family: 'JetBrains Mono', monospace;
        background: #334155;
        color: #94a3b8;
        padding: 2px 8px;
        border-radius: 4px;
      }
      .modal-cert-pill {
        font-size: 11px;
        font-weight: 700;
        padding: 2px 8px;
        border-radius: 9999px;
        text-transform: uppercase;
        letter-spacing: 0.5px;
      }
      .modal-cert-pill.cert-valid { background: rgba(34, 197, 94, 0.2); color: #4ade80; border: 1px solid #22c55e; }
      .modal-cert-pill.cert-invalid { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; }
      .modal-close-btn {
        background: transparent;
        border: none;
        color: #94a3b8;
        font-size: 24px;
        cursor: pointer;
        padding: 0 6px;
        line-height: 1;
        border-radius: 4px;
      }
      .modal-close-btn:hover { color: #f8fafc; background: #334155; }
      .modal-toolbar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 16px;
        background: #111827;
        border-bottom: 1px solid #1e293b;
        gap: 10px;
        flex-wrap: wrap;
      }
      .modal-toolbar-left {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
      }
      .modal-tool-btn {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        background: #1e293b;
        color: #e2e8f0;
        border: 1px solid #334155;
        padding: 6px 12px;
        border-radius: 6px;
        font-size: 12px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.15s;
      }
      .modal-tool-btn:hover { background: #334155; color: #ffffff; }
      .modal-tool-btn.primary { background: #2563eb; border-color: #3b82f6; color: #ffffff; }
      .modal-tool-btn.primary:hover { background: #1d4ed8; }
      .modal-search-input {
        background: #0f172a;
        border: 1px solid #334155;
        color: #f8fafc;
        padding: 5px 10px;
        border-radius: 6px;
        font-size: 12px;
        width: 140px;
      }
      .modal-search-input:focus { outline: none; border-color: #38bdf8; }
      .modal-body {
        flex: 1;
        overflow-y: auto;
        padding: 16px;
        background: #090d16;
      }
      .json-code-view {
        margin: 0;
        font-family: 'JetBrains Mono', Consolas, Monaco, monospace;
        font-size: 12px;
        line-height: 1.5;
        white-space: pre-wrap;
        word-break: break-all;
      }
      .json-key { color: #38bdf8; font-weight: 600; }
      .json-string { color: #a7f3d0; }
      .json-number { color: #f472b6; }
      .json-boolean { color: #fbbf24; font-weight: 700; }
      .json-null { color: #94a3b8; font-style: italic; }
      .modal-footer {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 12px 20px;
        background: #1e293b;
        border-top: 1px solid #334155;
      }
      .modal-metrics-chips {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
      }
      .modal-metrics-chips .chip {
        font-size: 11px;
        color: #94a3b8;
        background: #0f172a;
        padding: 3px 8px;
        border-radius: 4px;
        border: 1px solid #334155;
      }
      .modal-metrics-chips strong { color: #38bdf8; }
      .modal-dismiss-btn {
        background: transparent;
        border: 1px solid #475569;
        color: #94a3b8;
        padding: 6px 14px;
        border-radius: 6px;
        font-size: 12px;
        cursor: pointer;
      }
      .modal-dismiss-btn:hover { background: #334155; color: #ffffff; }

      /* Toast Notifications */
      .resplan-toast-container {
        position: fixed;
        bottom: 24px;
        right: 24px;
        z-index: 10000;
        display: flex;
        flex-direction: column;
        gap: 8px;
        pointer-events: none;
      }
      .resplan-toast {
        background: #1e293b;
        color: #f8fafc;
        border: 1px solid #334155;
        border-radius: 8px;
        padding: 10px 16px;
        font-size: 13px;
        box-shadow: 0 10px 15px -3px rgba(0,0,0,0.4);
        display: flex;
        align-items: center;
        gap: 8px;
        pointer-events: auto;
        animation: toastIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        transition: opacity 0.3s ease;
      }
      .resplan-toast.toast-success { border-left: 4px solid #22c55e; }
      .resplan-toast.toast-error { border-left: 4px solid #ef4444; }
      .resplan-toast.toast-warning { border-left: 4px solid #f59e0b; }
      .resplan-toast.toast-info { border-left: 4px solid #38bdf8; }
      .resplan-toast.fade-out { opacity: 0; }
      .toast-icon { font-weight: 800; font-size: 14px; }
      .toast-success .toast-icon { color: #4ade80; }
      .toast-error .toast-icon { color: #f87171; }
      .toast-warning .toast-icon { color: #fbbf24; }
      .toast-info .toast-icon { color: #38bdf8; }

      @keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }
      @keyframes toastIn { from { transform: translateY(12px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }
    `;
    document.head.appendChild(style);
  }

  function attachModalEvents() {
    const btnClose = document.getElementById('modalBtnClose');
    const btnDismiss = document.getElementById('modalBtnDismiss');
    const btnCopy = document.getElementById('modalBtnCopy');
    const btnDownJson = document.getElementById('modalBtnDownloadJson');
    const btnDownMd = document.getElementById('modalBtnDownloadMd');
    const btnDownHtml = document.getElementById('modalBtnDownloadHtml');
    const btnDownSvg = document.getElementById('modalBtnDownloadSvg');
    const searchInput = document.getElementById('modalSearchInput');

    if (btnClose) btnClose.addEventListener('click', closeModal);
    if (btnDismiss) btnDismiss.addEventListener('click', closeModal);

    if (btnCopy) {
      btnCopy.addEventListener('click', async () => {
        if (!currentModalData) return;
        const jsonStr = JSON.stringify(currentModalData, null, 2);
        try {
          await navigator.clipboard.writeText(jsonStr);
          btnCopy.classList.add('primary');
          const originalText = btnCopy.innerHTML;
          btnCopy.innerHTML = `<span>✓ Copied!</span>`;
          showToast('Slide 8 solution copied to clipboard', 'success');
          setTimeout(() => {
            btnCopy.innerHTML = originalText;
          }, 1800);
        } catch (err) {
          // Fallback
          const ta = document.createElement('textarea');
          ta.value = jsonStr;
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          showToast('Slide 8 solution copied to clipboard', 'success');
        }
      });
    }

    if (btnDownJson) {
      btnDownJson.addEventListener('click', () => {
        if (!currentModalData) return;
        const planId = currentModalData.plan_id || 'slide8';
        downloadText(
          JSON.stringify(currentModalData, null, 2),
          `resplan_${planId}_solution.json`,
          'application/json'
        );
        showToast(`Saved resplan_${planId}_solution.json`, 'success');
      });
    }

    if (btnDownMd) {
      btnDownMd.addEventListener('click', () => {
        const planId = (currentModalData && currentModalData.plan_id) || 'floor_plan';
        downloadMarkdownReport(planId);
      });
    }

    if (btnDownHtml) {
      btnDownHtml.addEventListener('click', () => {
        const planId = (currentModalData && currentModalData.plan_id) || 'floor_plan';
        downloadHtmlReport(planId);
      });
    }

    if (btnDownSvg) {
      btnDownSvg.addEventListener('click', () => {
        const planId = (currentModalData && currentModalData.plan_id) || 'floor_plan';
        downloadSvg(planId);
      });
    }

    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        const term = e.target.value.toLowerCase().trim();
        if (!currentModalData) return;
        if (!term) {
          document.getElementById('modalJsonCode').innerHTML = syntaxHighlightJson(currentModalData);
          return;
        }
        // Filter top-level keys matching term
        const filtered = {};
        for (const [k, v] of Object.entries(currentModalData)) {
          if (k.toLowerCase().includes(term) || JSON.stringify(v).toLowerCase().includes(term)) {
            filtered[k] = v;
          }
        }
        document.getElementById('modalJsonCode').innerHTML = syntaxHighlightJson(filtered);
      });
    }

    // Close on backdrop click
    modalElement.addEventListener('click', (e) => {
      if (e.target === modalElement) closeModal();
    });

    // Close on Escape
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && modalElement && modalElement.style.display !== 'none') {
        closeModal();
      }
    });
  }

  function openJsonModal(data, title) {
    ensureModal();
    currentModalData = data;

    const modalTitle = document.getElementById('modalTitle');
    const modalPlanBadge = document.getElementById('modalPlanBadge');
    const modalCertPill = document.getElementById('modalCertPill');
    const codeEl = document.getElementById('modalJsonCode');

    if (modalTitle) modalTitle.textContent = title || 'Slide 8 Solution JSON Inspector';
    if (modalPlanBadge) modalPlanBadge.textContent = `Plan: ${data.plan_id || '--'}`;

    const isValid = data.certified !== false && (data.checker_validation ? data.checker_validation.status === 'VALID' : true);
    if (modalCertPill) {
      if (isValid) {
        modalCertPill.className = 'modal-cert-pill cert-valid';
        modalCertPill.textContent = 'CERTIFIED (PASS)';
      } else {
        modalCertPill.className = 'modal-cert-pill cert-invalid';
        modalCertPill.textContent = 'UNCERTIFIED (FAIL)';
      }
    }

    // Populate score chips
    const metrics = data.metrics || {};
    const setChip = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val !== undefined ? val : '--';
    };
    setChip('mChipH', metrics.hubs || (data.hubs ? data.hubs.length : '--'));
    setChip('mChipL', metrics.cable_length_m !== undefined ? metrics.cable_length_m.toFixed(2) : '--');
    setChip('mChipJ', metrics.total_cost_J !== undefined ? metrics.total_cost_J.toFixed(2) : '--');
    setChip('mChipB', metrics.bends);
    setChip('mChipX', metrics.crossings);
    setChip('mChipA', metrics.area_gap_m2 !== undefined ? metrics.area_gap_m2.toFixed(1) : '--');

    if (codeEl) {
      codeEl.innerHTML = syntaxHighlightJson(data);
    }

    modalElement.style.display = 'flex';
  }

  function closeModal() {
    if (modalElement) modalElement.style.display = 'none';
  }

  // --- Core API Download Operations ---

  /**
   * Download certified official Slide 8 JSON solution.
   */
  async function downloadOfficialJson(options = {}) {
    try {
      showToast('Fetching certified Slide 8 solution...', 'info');

      // Try certified JSON endpoint first, fall back to general export
      let res = await fetch(EXPORT_CONFIG.endpoints.certifiedJson);
      if (!res.ok && res.status === 404) {
        res = await fetch(EXPORT_CONFIG.endpoints.fallbackJson);
      }

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        if (res.status === 422 && errData.error === 'UNCERTIFIED_SOLUTION') {
          showToast(`Refused to export uncertified solution: ${errData.message}`, 'error');
          if (options.showModalOnError) {
            openJsonModal(errData, 'Uncertified Solution Diagnostic');
          }
          return null;
        }
        throw new Error(`Export request failed (${res.status}): ${errData.message || res.statusText}`);
      }

      const data = await res.json();
      const planId = data.plan_id || 'solution';

      // Check validation
      if (data.certified === false) {
        showToast(`Warning: Solution has rule violations and is uncertified.`, 'warning');
      }

      const filename = `resplan_${planId}_solution.json`;
      downloadText(JSON.stringify(data, null, 2), filename, 'application/json');
      showToast(`Downloaded official solution: ${filename}`, 'success');

      if (options.inspectAfterDownload) {
        openJsonModal(data, 'Slide 8 Solution Exported');
      }

      return data;
    } catch (err) {
      showToast(`Export error: ${err.message}`, 'error');
      console.error('[ResPlanExporter] downloadOfficialJson failed:', err);
      throw err;
    }
  }

  /**
   * Download Technical Summary Report in Markdown (.md).
   */
  async function downloadMarkdownReport(planId = 'floor_plan') {
    try {
      showToast('Generating technical summary markdown...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.reportMd);
      if (!res.ok) throw new Error(`Report generation failed (${res.status})`);

      const mdText = await res.text();
      const filename = `resplan_${planId}_report.md`;
      downloadText(mdText, filename, 'text/markdown;charset=utf-8');
      showToast(`Downloaded technical report: ${filename}`, 'success');
      return mdText;
    } catch (err) {
      showToast(`Report download failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Download printable Technical Summary Report in HTML (.html).
   */
  async function downloadHtmlReport(planId = 'floor_plan') {
    try {
      showToast('Generating printable HTML report...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.reportHtml);
      if (!res.ok) throw new Error(`HTML report generation failed (${res.status})`);

      const htmlText = await res.text();
      const filename = `resplan_${planId}_report.html`;
      downloadText(htmlText, filename, 'text/html;charset=utf-8');
      showToast(`Downloaded printable report: ${filename}`, 'success');
      return htmlText;
    } catch (err) {
      showToast(`HTML report download failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Download Scalable Vector Graphics (SVG) schematic.
   */
  async function downloadSvg(planId = 'floor_plan') {
    try {
      showToast('Generating vector SVG schematic...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.schematicSvg);
      if (!res.ok) throw new Error(`SVG generation failed (${res.status})`);

      const svgText = await res.text();
      const filename = `resplan_${planId}_schematic.svg`;
      downloadText(svgText, filename, 'image/svg+xml;charset=utf-8');
      showToast(`Downloaded vector schematic: ${filename}`, 'success');
      return svgText;
    } catch (err) {
      showToast(`SVG download failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Download high-resolution PNG raster overlay schematic.
   */
  async function downloadPng(planId = 'floor_plan') {
    try {
      showToast('Rendering high-res overlay PNG...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.overlayPng);
      if (!res.ok) throw new Error(`PNG export failed (${res.status})`);

      const blob = await res.blob();
      const filename = `resplan_${planId}_overlay.png`;
      downloadBlob(blob, filename);
      showToast(`Downloaded raster overlay: ${filename}`, 'success');
      return blob;
    } catch (err) {
      showToast(`PNG export failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Download comparison summary in CSV format.
   */
  async function downloadComparisonCsv(runs, filename = 'resplan_comparison.csv') {
    try {
      showToast('Exporting comparison matrix (CSV)...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.comparisonCsv, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(runs || []),
      });
      if (!res.ok) throw new Error(`Comparison CSV export failed (${res.status})`);

      const csvText = await res.text();
      downloadText(csvText, filename, 'text/csv;charset=utf-8');
      showToast(`Downloaded comparison matrix: ${filename}`, 'success');
      return csvText;
    } catch (err) {
      showToast(`Comparison CSV failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Download comparison summary in JSON format.
   */
  async function downloadComparisonJson(runs, filename = 'resplan_comparison.json') {
    try {
      showToast('Exporting comparison matrix (JSON)...', 'info');
      const res = await fetch(EXPORT_CONFIG.endpoints.comparisonJson, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(runs || []),
      });
      if (!res.ok) throw new Error(`Comparison JSON export failed (${res.status})`);

      const jsonText = await res.text();
      downloadText(jsonText, filename, 'application/json;charset=utf-8');
      showToast(`Downloaded comparison JSON: ${filename}`, 'success');
      return jsonText;
    } catch (err) {
      showToast(`Comparison JSON failed: ${err.message}`, 'error');
      throw err;
    }
  }

  /**
   * Inspect current solution by fetching live export and opening modal.
   */
  async function inspectCurrentSolution() {
    try {
      showToast('Loading solution for inspection...', 'info');
      let res = await fetch(EXPORT_CONFIG.endpoints.certifiedJson);
      if (!res.ok && res.status === 404) {
        res = await fetch(EXPORT_CONFIG.endpoints.fallbackJson);
      }
      if (!res.ok) throw new Error(`Fetch failed (${res.status})`);

      const data = await res.json();
      openJsonModal(data, 'Slide 8 Solution JSON Inspector');
      return data;
    } catch (err) {
      showToast(`Inspection failed: ${err.message}`, 'error');
      throw err;
    }
  }

  // --- Auto-Binding & Initialization ---

  function init() {
    // 1. Ensure modal structure & styles are prepared
    ensureModal();

    // 2. Wire up existing DOM export buttons if present
    const btnExportSlide8 = document.getElementById('btnExportSlide8');
    if (btnExportSlide8) {
      // Add right-click or alt-click context for modal inspection
      btnExportSlide8.setAttribute('title', 'Click to Download Slide 8 JSON (Ctrl+Click to Inspect in Modal)');
      btnExportSlide8.addEventListener('click', (e) => {
        if (e.ctrlKey || e.metaKey || e.shiftKey) {
          e.preventDefault();
          e.stopPropagation();
          inspectCurrentSolution();
        }
      });
    }

    // 3. Inject dedicated "Inspect Solution" button if not already in DOM
    const actionContainer = document.querySelector('.reviewer-actions-card');
    if (actionContainer && !document.getElementById('btnInspectSlide8')) {
      const inspectBtn = document.createElement('button');
      inspectBtn.id = 'btnInspectSlide8';
      inspectBtn.className = 'action-btn secondary';
      inspectBtn.title = 'Inspect Slide 8 Solution JSON with Syntax Highlighting and Copy';
      inspectBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
        </svg>
        Inspect Solution JSON
      `;
      inspectBtn.addEventListener('click', inspectCurrentSolution);

      // Insert adjacent to btnExportSlide8
      if (btnExportSlide8 && btnExportSlide8.parentNode) {
        btnExportSlide8.parentNode.insertBefore(inspectBtn, btnExportSlide8.nextSibling);
      } else {
        actionContainer.appendChild(inspectBtn);
      }
    }

    // 4. Inject dedicated "Download Report" button if not already in DOM
    if (actionContainer && !document.getElementById('btnDownloadReportDropdown')) {
      const reportBtn = document.createElement('button');
      reportBtn.id = 'btnDownloadReportDropdown';
      reportBtn.className = 'action-btn secondary';
      reportBtn.title = 'Download Technical Summary Certification Report';
      reportBtn.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
          <polyline points="10 9 9 9 8 9"/>
        </svg>
        Download Technical Report
      `;
      reportBtn.addEventListener('click', () => {
        const planId = (currentModalData && currentModalData.plan_id) || 'floor_plan';
        downloadHtmlReport(planId);
      });

      const pngBtn = document.getElementById('btnDownloadOverlayPng');
      if (pngBtn && pngBtn.parentNode) {
        pngBtn.parentNode.insertBefore(reportBtn, pngBtn.nextSibling);
      } else {
        actionContainer.appendChild(reportBtn);
      }
    }
  }

  // Auto-initialize when DOM is ready
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      setTimeout(init, 50);
    }
  }

  // --- Public Interface ---
  return {
    downloadOfficialJson,
    downloadMarkdownReport,
    downloadHtmlReport,
    downloadSvg,
    downloadPng,
    downloadComparisonCsv,
    downloadComparisonJson,
    inspectCurrentSolution,
    openJsonModal,
    closeModal,
    showToast,
    syntaxHighlightJson,
    init,
  };
});
