/**
 * TAN Supply Chain — UI Component Module
 *
 * All UI is built from pure functions that return HTML strings.
 * No framework dependency — drop into any page with a <script> tag.
 *
 * Architecture notes for future Figma MCP integration:
 *   Each component is isolated and receives only data (no DOM coupling).
 *   To apply a new design system, swap the template literals inside each
 *   function. The calling code (renderReport, renderSupplierChain, etc.)
 *   does not need to change — it passes the same data shape.
 *
 * Usage:
 *   <script src="components.js"></script>
 *   document.getElementById('wrap').innerHTML = Components.designationHero(data);
 */

const Components = (() => {
  'use strict';

  // ── Utility helpers ────────────────────────────────────────────────────────

  /** Escape user-controlled strings before injecting into innerHTML. */
  function esc(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /** Truncate a hex ID for display: "abc123...def456" */
  function shortId(id, head = 10, tail = 6) {
    if (!id || id.length <= head + tail + 3) return esc(id);
    return `${esc(id.slice(0, head))}…${esc(id.slice(-tail))}`;
  }

  /** Format a CAD dollar amount with commas and two decimal places. */
  function fmtCAD(n) {
    return '$' + Number(n).toLocaleString('en-CA', {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  /** Map designation string → CSS class suffix. */
  function designationClass(d) {
    const map = {
      'Product of Canada': 'product-of-canada',
      'Made in Canada':    'made-in-canada',
      'Not Qualified':     'not-qualified',
    };
    return map[d] || 'unknown';
  }

  /** Bar colour class based on Canadian content percentage. */
  function barClass(pct) {
    if (pct >= 98) return 'high';
    if (pct >= 51) return 'med';
    return 'low';
  }

  // ── Badge ──────────────────────────────────────────────────────────────────

  /**
   * Badge component.
   * @param {string} text  - Label text
   * @param {'success'|'warning'|'danger'|'info'} variant
   */
  function badge(text, variant = 'info') {
    return `<span class="badge badge-${esc(variant)}">${esc(text)}</span>`;
  }

  // ── Alert ──────────────────────────────────────────────────────────────────

  /**
   * Alert banner component.
   * @param {string} message
   * @param {'success'|'warning'|'danger'|'info'} variant
   */
  function alert(message, variant = 'info') {
    return `<div class="alert alert-${esc(variant)}">${esc(message)}</div>`;
  }

  // ── Spinner ────────────────────────────────────────────────────────────────

  /** Inline loading spinner. */
  function spinner(label = 'Loading…') {
    return `<span class="spinner" aria-label="${esc(label)}"></span> ${esc(label)}`;
  }

  // ── Stat card ─────────────────────────────────────────────────────────────

  /**
   * Individual metric card (for use inside a .stat-grid).
   * @param {string} value  - Large display value (already formatted)
   * @param {string} label  - Small uppercase label below the value
   */
  function statCard(value, label) {
    return `
      <div class="stat-card">
        <div class="stat-value">${value}</div>
        <div class="stat-label">${esc(label)}</div>
      </div>`;
  }

  // ── Designation hero ──────────────────────────────────────────────────────

  /**
   * Full-width hero banner showing the Canadian content designation.
   *
   * @param {{
   *   designation: string,
   *   canadian_content_pct: number,
   *   last_transformation_in_canada: boolean
   * }} data
   */
  function designationHero(data) {
    const pct = Number(data.canadian_content_pct || 0);
    const cls = designationClass(data.designation);
    const lastXform = data.last_transformation_in_canada ? 'Yes' : 'No';

    return `
      <div class="designation-hero designation-${cls}">
        <div class="designation-pct">${pct.toFixed(1)}%</div>
        <div class="designation-label">Canadian Content</div>
        <div class="designation-title" style="margin-top:.75rem;">
          ${esc(data.designation)}
        </div>
        <div style="font-size:.8rem;opacity:.8;margin-top:.3rem;">
          Last transformation in Canada: ${lastXform}
        </div>
      </div>`;
  }

  // ── Stats grid ─────────────────────────────────────────────────────────────

  /**
   * Four-card metric grid for a provenance report.
   *
   * @param {{
   *   total_cost_cad: number,
   *   canadian_cost_cad: number,
   *   chain_length: number,
   *   anomalies: Array
   * }} data
   */
  function statsGrid(data) {
    const hasAnomalies = data.anomalies && data.anomalies.length > 0;
    const anomalyBadge = hasAnomalies
      ? badge(`${data.anomalies.length} Issues`, 'danger')
      : badge('Clean', 'success');

    return `
      <div class="stat-grid">
        ${statCard(fmtCAD(data.total_cost_cad), 'Total Cost (CAD)')}
        ${statCard(fmtCAD(data.canadian_cost_cad), 'Canadian Cost (CAD)')}
        ${statCard(String(data.chain_length), 'Chain Depth')}
        ${statCard(anomalyBadge, 'Anomalies')}
      </div>`;
  }

  // ── Canadian content progress bar + breakdown table ───────────────────────

  /**
   * Progress bar + cost breakdown table card.
   *
   * @param {{
   *   canadian_cost_cad: number,
   *   canadian_content_pct: number,
   *   cost_breakdown: Array<{name,type,country,cost_cad,is_canadian}>
   * }} data
   */
  function contentBreakdown(data) {
    const pct = Number(data.canadian_content_pct || 0);
    const cls = barClass(pct);

    const rows = (data.cost_breakdown || []).map(row => `
      <tr>
        <td>${esc(row.name)}</td>
        <td>${badge(row.type, 'info')}</td>
        <td>${esc(row.country)}</td>
        <td>${fmtCAD(row.cost_cad)}</td>
        <td>${badge(row.is_canadian ? 'Yes' : 'No', row.is_canadian ? 'success' : 'danger')}</td>
      </tr>`).join('');

    return `
      <div class="card">
        <div class="card-title">Canadian Content Breakdown</div>
        <div style="display:flex;justify-content:space-between;font-size:.85rem;margin-bottom:.4rem;">
          <span>${fmtCAD(data.canadian_cost_cad)} Canadian</span>
          <span>${pct.toFixed(1)}%</span>
        </div>
        <div class="progress-wrap">
          <div class="progress-bar ${cls}" style="width:${Math.min(pct, 100)}%"></div>
        </div>
        <div style="display:flex;justify-content:space-between;margin-top:.4rem;font-size:.75rem;color:var(--tan-gray);">
          <span>0%</span>
          <span style="color:var(--tan-warning);">51% Made in Canada</span>
          <span style="color:var(--tan-blue);">98% Product of Canada</span>
          <span>100%</span>
        </div>
        <div class="table-scroll" style="margin-top:1.25rem;">
          <table>
            <thead>
              <tr>
                <th>Component</th><th>Type</th><th>Country</th>
                <th>Cost (CAD)</th><th>Canadian?</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  // ── Anomaly list ──────────────────────────────────────────────────────────

  /**
   * Anomaly cards or "clean" confirmation.
   *
   * @param {Array<{type:string, detail:string, attestation_id?:string}>} anomalies
   */
  function anomalyList(anomalies) {
    if (!anomalies || anomalies.length === 0) {
      return `
        <div class="card">
          ${alert('No anomalies detected — chain integrity verified.', 'success')}
        </div>`;
    }

    const items = anomalies.map(a => `
      <div class="anomaly-item">
        <div>
          <div class="anomaly-type">${esc(a.type || 'anomaly')}</div>
          <div style="margin-top:.2rem;">${esc(a.detail || '')}</div>
          ${a.attestation_id
            ? `<div class="mono" style="margin-top:.3rem;">${shortId(a.attestation_id)}</div>`
            : ''}
        </div>
      </div>`).join('');

    return `
      <div class="card">
        <div class="card-title" style="color:var(--tan-danger);">
          Anomalies Detected
        </div>
        ${items}
      </div>`;
  }

  // ── Supply chain DAG visualisation ────────────────────────────────────────

  /**
   * Vertical chain-of-custody diagram.
   *
   * @param {Array<{
   *   attestation_id: string,
   *   product_name: string,
   *   supplier_id: string,
   *   location: {country:string, province?:string},
   *   inputs: string[],
   *   sig_valid: boolean
   * }>} nodes
   */
  function supplyChain(nodes) {
    if (!nodes || nodes.length === 0) {
      return `<div class="card">${alert('No chain data available.', 'warning')}</div>`;
    }

    const nodeHTML = nodes.map((node, i) => {
      const dotClass = i === 0 ? 'root' : node.sig_valid === false ? 'invalid' : '';
      const loc = node.location || {};
      const locationStr = [loc.country, loc.province].filter(Boolean).join(', ');
      const inputList = (node.inputs || []).length
        ? `<div style="font-size:.75rem;color:var(--tan-gray);margin-top:.3rem;">
             Inputs: ${(node.inputs || []).map(id => shortId(id)).join(', ')}
           </div>`
        : '';

      return `
        <div class="chain-node">
          <div class="chain-dot ${dotClass}">${i + 1}</div>
          <div class="chain-content">
            <div class="chain-name">${esc(node.product_name)}</div>
            <div class="chain-meta">
              Supplier: <strong>${esc(node.supplier_id)}</strong>
              ${locationStr ? `&bull; ${esc(locationStr)}` : ''}
              &bull;
              ${badge(node.sig_valid ? 'Sig OK' : 'Sig FAIL', node.sig_valid ? 'success' : 'danger')}
            </div>
            <div class="mono" style="margin-top:.3rem;">${shortId(node.attestation_id)}</div>
            ${inputList}
          </div>
        </div>`;
    }).join('');

    return `
      <div class="card">
        <div class="card-title">Supply Chain</div>
        <div id="chain-vis">${nodeHTML}</div>
      </div>`;
  }

  // ── QR code display ───────────────────────────────────────────────────────

  /**
   * QR code image card.
   * @param {string} apiBase   - e.g. "http://localhost:8000"
   * @param {string} attId     - attestation ID
   */
  function qrCard(apiBase, attId) {
    const src = `${apiBase}/api/qr/${encodeURIComponent(attId)}`;
    return `
      <div class="card">
        <div class="card-title">Share via QR Code</div>
        <div class="qr-wrap">
          <img src="${esc(src)}" alt="QR Code" width="200" height="200" />
        </div>
        <div style="text-align:center;margin-top:.5rem;">
          <div class="mono">${esc(attId)}</div>
        </div>
      </div>`;
  }

  // ── Full provenance report ────────────────────────────────────────────────

  /**
   * Compose a complete provenance report from API response data.
   * Replaces the inline renderReport() function in purchaser.html.
   *
   * @param {object} data  - Response from GET /api/product/:id
   * @param {string} apiBase
   */
  function provenanceReport(data, apiBase) {
    return [
      designationHero(data),
      statsGrid(data),
      contentBreakdown(data),
      anomalyList(data.anomalies),
      supplyChain(data.supplier_chain),
      qrCard(apiBase, data.product_attestation_id),
    ].join('\n');
  }

  // ── Dashboard supplier row ────────────────────────────────────────────────

  /**
   * A single <tr> for the dashboard supplier table.
   * @param {{supplier_id, name, country, province, created_at}} supplier
   */
  function supplierRow(supplier) {
    const loc = [supplier.country, supplier.province].filter(Boolean).join(', ');
    const date = supplier.created_at
      ? new Date(supplier.created_at).toLocaleDateString('en-CA')
      : '—';
    return `
      <tr>
        <td><span class="mono">${esc(supplier.supplier_id)}</span></td>
        <td>${esc(supplier.name)}</td>
        <td>${esc(loc)}</td>
        <td>${date}</td>
      </tr>`;
  }

  // ── Dashboard attestation row ─────────────────────────────────────────────

  /**
   * A single <tr> for the dashboard attestation table.
   * @param {{id, product_name, signer_id, sig_valid, created_at}} att
   * @param {string} purchaserBase  - base URL for the purchaser page
   */
  function attestationRow(att, purchaserBase = '') {
    const date = att.created_at
      ? new Date(att.created_at).toLocaleDateString('en-CA')
      : '—';
    const link = purchaserBase
      ? `<a href="${esc(purchaserBase)}/purchaser.html?id=${esc(att.id)}"
            style="color:var(--tan-blue);">${esc(att.product_name || '—')}</a>`
      : esc(att.product_name || '—');

    return `
      <tr>
        <td>${link}</td>
        <td><span class="mono">${shortId(att.id)}</span></td>
        <td>${esc(att.signer_id)}</td>
        <td>${badge(att.sig_valid ? 'Valid' : 'Invalid', att.sig_valid ? 'success' : 'danger')}</td>
        <td>${date}</td>
      </tr>`;
  }

  // ── Public API ────────────────────────────────────────────────────────────

  return {
    // Primitives
    badge,
    alert,
    spinner,
    statCard,
    // Composite
    designationHero,
    statsGrid,
    contentBreakdown,
    anomalyList,
    supplyChain,
    qrCard,
    provenanceReport,
    // Table rows
    supplierRow,
    attestationRow,
    // Utilities (exposed for testing)
    _esc: esc,
    _shortId: shortId,
    _fmtCAD: fmtCAD,
  };
})();
