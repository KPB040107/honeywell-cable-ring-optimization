/**
 * ResPlan Architectural Co-Design Studio — Interactive Canvas & Live Reviewer HUD
 *
 * Implements:
 * - 60 FPS HTML5 Canvas rendering of metric floor plans, rooms, walls, halos, hubs, and routes.
 * - Real-time pointer interaction: drag-and-drop nodes and hubs with live 0.5m clearance halos.
 * - Sub-50 ms WebSocket streaming with auto-reconnection and HTTP mutation fallback.
 * - Live Reviewer HUD with Competition Metrics Scoreboard and Slide 3 Hard Validity Checklist.
 * - One-click Slide 8 submission JSON export.
 */

(function () {
  'use strict';

  // --- Configuration & Constants ---
  const CONFIG = {
    wsUrl: `ws://${window.location.host}/ws/co-design`,
    httpFallbackBase: `${window.location.origin}`,
    haloRadiusM: 0.50,
    haloSafeColor: 'rgba(46, 204, 113, 0.35)',
    haloSafeStroke: '#2ECC71',
    haloViolColor: 'rgba(230, 25, 75, 0.45)',
    haloViolStroke: '#E6194B',
    hubColors: ['#1F77B4', '#E6550D', '#2CA02C', '#9467BD', '#8C564B'],
    roomPastelPalette: {
      living: '#DCE8F8',
      bedroom: '#FDE5D2',
      kitchen: '#E8F5E9',
      corridor: '#F3E8FF',
      bathroom: '#E2E8F0',
      balcony: '#FEF3C7',
      default: '#F1F5F9',
    },
  };

  // --- State Store ---
  const state = {
    ws: null,
    wsConnected: false,
    reconnectTimer: null,
    inFlightAction: false,
    lastPingTime: 0,
    measuredLatencyMs: 15,
    showHalos: true,
    showBadges: true,
    showGhostDoors: true,
    copilotProposals: [],
    activeProposalId: null,
    copilotLoading: false,

    // Floor plan data from backend
    serverState: null,

    // Pan & Zoom Transform
    transform: {
      scale: 40, // pixels per metre
      offsetX: 60,
      offsetY: 60,
      minX: 0,
      maxX: 14,
      minY: 0,
      maxY: 9,
    },

    // Pointer Interaction
    dragTarget: null, // { type: 'node' | 'hub', id: string, initialX, initialY, currentX, currentY }
    isPanning: false,
    panStart: { x: 0, y: 0 },
    mouseWorldPos: { x: 0, y: 0 },

    // Animation & FPS
    frameCount: 0,
    lastFpsTime: performance.now(),
    currentFps: 60,

    // Wire-Cut Survivability (Phase 9)
    scissorsMode: false,
    hoveredCable: null,
    activeCutReport: null,
    pulsePhase: 0,

    // 3D BIM Viewport (Phase 10)
    viewMode: 'split',
    bimSceneData: null,
    bimSceneLoading: false,
  };

  // --- Three.js 3D BIM Engine State ---
  const bim3d = {
    renderer: null,
    scene: null,
    camera: null,
    controls: null,
    container: null,
    elementsGroup: null,
    survivabilityGroup: null,
    gridHelper: null,
    dirLight: null,
    ambientLight: null,
    isInitialized: false,
  };

  // --- DOM Elements ---
  const canvas = document.getElementById('studioCanvas');
  const ctx = canvas.getContext('2d');
  const canvasWrapper = document.getElementById('canvasWrapper');

  const connPill = document.getElementById('connPill');
  const connStatus = document.getElementById('connStatus');
  const fpsReadout = document.getElementById('fpsReadout');
  const latencyReadout = document.getElementById('latencyReadout');
  const coordDisplay = document.getElementById('coordDisplay');

  const metricJ = document.getElementById('metricJ');
  const metricL = document.getElementById('metricL');
  const metricB = document.getElementById('metricB');
  const metricX = document.getElementById('metricX');
  const metricO = document.getElementById('metricO');
  const metricH = document.getElementById('metricH');
  const areaGapVal = document.getElementById('areaGapVal');

  const validityBanner = document.getElementById('validityBanner');
  const validityTitle = document.getElementById('validityTitle');
  const validitySub = document.getElementById('validitySub');
  const bannerIconPass = document.getElementById('bannerIconPass');
  const bannerIconFail = document.getElementById('bannerIconFail');
  const rulesScoreBadge = document.getElementById('rulesScoreBadge');

  const diagnosticsBox = document.getElementById('diagnosticsBox');
  const diagnosticsList = document.getElementById('diagnosticsList');
  const hubsContainer = document.getElementById('hubsContainer');

  const chkHalos = document.getElementById('chkHalos');
  const chkBadges = document.getElementById('chkBadges');
  const chkGhostDoors = document.getElementById('chkGhostDoors');
  const btnSuggestToolbar = document.getElementById('btnSuggestToolbar');
  const btnSuggestActions = document.getElementById('btnSuggestActions');
  const btnRefreshCopilot = document.getElementById('btnRefreshCopilot');
  const copilotContainer = document.getElementById('copilotContainer');

  const btnScissorsTool = document.getElementById('btnScissorsTool');
  const btnHealCircuit = document.getElementById('btnHealCircuit');

  const btnZoomIn = document.getElementById('btnZoomIn');
  const btnZoomOut = document.getElementById('btnZoomOut');
  const btnFitScreen = document.getElementById('btnFitScreen');
  const btnResetView = document.getElementById('btnResetView');
  const btnExportSlide8 = document.getElementById('btnExportSlide8');
  const btnResetBaseline = document.getElementById('btnResetBaseline');
  const btnAutoOptimize = document.getElementById('btnAutoOptimize');

  // --- Coordinate Transformation Utilities ---
  function worldToScreen(wx, wy) {
    // In Cartesian floor plans, wy increases upwards, but screen y increases downwards
    const sx = state.transform.offsetX + (wx - state.transform.minX) * state.transform.scale;
    const sy = canvas.height / window.devicePixelRatio - (state.transform.offsetY + (wy - state.transform.minY) * state.transform.scale);
    return { x: sx, y: sy };
  }

  function screenToWorld(sx, sy) {
    const wx = state.transform.minX + (sx - state.transform.offsetX) / state.transform.scale;
    const canvasH = canvas.height / window.devicePixelRatio;
    const wy = state.transform.minY + (canvasH - sy - state.transform.offsetY) / state.transform.scale;
    return { x: wx, y: wy };
  }

  function projectPointToSegment(pt, a, b) {
    const abx = b.x - a.x;
    const aby = b.y - a.y;
    const lenSq = abx * abx + aby * aby;
    if (lenSq === 0) return { x: a.x, y: a.y };
    let t = ((pt.x - a.x) * abx + (pt.y - a.y) * aby) / lenSq;
    t = Math.max(0, Math.min(1, t));
    return {
      x: a.x + t * abx,
      y: a.y + t * aby,
    };
  }

  function findNearestCablePoint(worldX, worldY, hitRadiusM = 0.35) {
    if (!state.serverState || !state.serverState.routes) return null;
    let closest = null;
    let minD = hitRadiusM;

    state.serverState.routes.forEach((ring, rIdx) => {
      (ring.segments || []).forEach((seg, sIdx) => {
        const pts = seg.polyline || [];
        for (let i = 0; i < pts.length - 1; i++) {
          const p1 = { x: pts[i][0], y: pts[i][1] };
          const p2 = { x: pts[i + 1][0], y: pts[i + 1][1] };
          const proj = projectPointToSegment({ x: worldX, y: worldY }, p1, p2);
          const d = Math.hypot(proj.x - worldX, proj.y - worldY);
          if (d < minD) {
            minD = d;
            closest = {
              hubId: ring.hub_id,
              ringIdx: rIdx,
              segIdx: sIdx,
              segment: seg,
              cutPoint: proj,
              distance: d,
            };
          }
        }
      });
    });
    return closest;
  }

  function fitToScreen() {
    if (!state.serverState || !state.serverState.plan) return;
    const rooms = state.serverState.plan.rooms || [];
    let minX = 0, maxX = 14, minY = 0, maxY = 9;

    if (rooms.length > 0) {
      minX = Infinity; maxX = -Infinity; minY = Infinity; maxY = -Infinity;
      rooms.forEach(r => {
        (r.polygon || []).forEach(pt => {
          if (pt[0] < minX) minX = pt[0];
          if (pt[0] > maxX) maxX = pt[0];
          if (pt[1] < minY) minY = pt[1];
          if (pt[1] > maxY) maxY = pt[1];
        });
      });
    }

    state.transform.minX = minX;
    state.transform.maxX = maxX;
    state.transform.minY = minY;
    state.transform.maxY = maxY;

    const w = maxX - minX || 14;
    const h = maxY - minY || 9;

    const rect = canvasWrapper.getBoundingClientRect();
    const pad = 60;
    const availW = rect.width - pad * 2;
    const availH = rect.height - pad * 2;

    const scaleX = availW / w;
    const scaleY = availH / h;
    state.transform.scale = Math.min(scaleX, scaleY);
    state.transform.offsetX = (rect.width - w * state.transform.scale) / 2;
    state.transform.offsetY = (rect.height - h * state.transform.scale) / 2;
  }

  // --- WebSocket & Network Layer ---
  function connectWebSocket() {
    if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    updateConnectionUI('connecting', 'Connecting...');

    try {
      state.ws = new WebSocket(CONFIG.wsUrl);
    } catch (e) {
      console.warn('WebSocket init failed, fallback to HTTP', e);
      fetchInitialStateHTTP();
      return;
    }

    state.ws.onopen = function () {
      state.wsConnected = true;
      updateConnectionUI('connected', 'Connected: 60 FPS');
      // Request initial state
      sendWebSocketMessage({ action: 'get_state' });
    };

    state.ws.onmessage = function (event) {
      try {
        const payload = JSON.parse(event.data);
        handleServerPayload(payload);
      } catch (err) {
        console.error('Failed to parse WebSocket message:', err);
      }
    };

    state.ws.onclose = function () {
      state.wsConnected = false;
      updateConnectionUI('disconnected', 'Disconnected (Retrying)');
      scheduleReconnect();
    };

    state.ws.onerror = function (err) {
      console.warn('WebSocket error:', err);
      state.wsConnected = false;
    };
  }

  function scheduleReconnect() {
    if (state.reconnectTimer) clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(connectWebSocket, 2000);
  }

  function sendWebSocketMessage(msg) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.lastPingTime = performance.now();
      state.ws.send(JSON.stringify(msg));
      state.inFlightAction = true;
    } else {
      // HTTP mutation fallback
      performHttpMutation(msg);
    }
  }

  async function performHttpMutation(msg) {
    const action = msg.action;
    try {
      const t0 = performance.now();
      let res;
      if (action === 'drag_node') {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/node/drag`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ node_id: msg.node_id, x: msg.x, y: msg.y }),
        });
      } else if (action === 'drag_hub') {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/hub/drag`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hub_id: msg.hub_id, x: msg.x, y: msg.y }),
        });
      } else if (action === 'reset') {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/reset`, { method: 'POST' });
      } else if (action === 'wire_cut') {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/survivability/cut`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ hub_id: msg.hub_id, cut_x: msg.cut_x, cut_y: msg.cut_y }),
        });
      } else if (action === 'wire_heal') {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/survivability/heal`, {
          method: 'POST',
        });
      } else {
        res = await fetch(`${CONFIG.httpFallbackBase}/api/state`);
      }

      const elapsed = performance.now() - t0;
      state.measuredLatencyMs = elapsed;
      if (res && res.ok) {
        const data = await res.json();
        handleServerPayload(data);
      }
    } catch (e) {
      console.warn('HTTP fallback request failed:', e);
    }
  }

  async function fetchInitialStateHTTP() {
    try {
      const res = await fetch(`${CONFIG.httpFallbackBase}/api/state`);
      if (res.ok) {
        const data = await res.json();
        handleServerPayload(data);
      }
    } catch (e) {
      console.warn('Could not fetch initial state via HTTP:', e);
    }
  }

  function handleServerPayload(data) {
    if (state.lastPingTime > 0) {
      const rtt = performance.now() - state.lastPingTime;
      state.measuredLatencyMs = rtt;
      state.lastPingTime = 0;
    }
    state.inFlightAction = false;

    if (data.type === 'survivability_update') {
      state.serverState = data;
      state.activeCutReport = data.report || data.survivability_report || null;
      updateSurvivabilityHUD(state.activeCutReport);
      updateHUD(data);
      if (data.bim_scene) {
        state.bimSceneData = data.bim_scene;
        rebuildBIMMeshes(data.bim_scene);
      } else {
        fetchBIMScene();
      }
      updateBIMSurvivability();
    } else if (data.type === 'state_update') {
      const firstLoad = !state.serverState;
      state.serverState = data;
      if (firstLoad) {
        fitToScreen();
      }
      state.activeCutReport = data.survivability_report || null;
      updateSurvivabilityHUD(state.activeCutReport);
      updateHUD(data);
      if (data.bim_scene) {
        state.bimSceneData = data.bim_scene;
        rebuildBIMMeshes(data.bim_scene);
      } else {
        fetchBIMScene();
      }
      updateBIMSurvivability();
      if (state.activeProposalId) {
        state.activeProposalId = null;
        state.copilotProposals = [];
        renderCopilotProposals([], null);
      }
    } else if (data.type === 'bim_scene_update') {
      state.bimSceneData = data.scene;
      rebuildBIMMeshes(data.scene);
    } else if (data.type === 'copilot_proposals') {
      state.copilotLoading = false;
      const report = data.report || {};
      state.copilotProposals = report.recommended_proposals || [];
      renderCopilotProposals(state.copilotProposals, report);
    }
  }

  function updateSurvivabilityHUD(report) {
    const survBanner = document.getElementById('survStatusBanner');
    const survGaugeVal = document.getElementById('survGaugeVal');
    const survGaugeSub = document.getElementById('survGaugeSub');
    const survFaultBox = document.getElementById('survFaultBox');
    const survStandardBadge = document.getElementById('survStandardBadge');
    const faultLeg = document.getElementById('faultLeg');
    const faultCoords = document.getElementById('faultCoords');
    const outboundNodesList = document.getElementById('outboundNodesList');
    const returnNodesList = document.getElementById('returnNodesList');

    if (!survBanner) return;

    if (report && report.is_severed) {
      survBanner.className = 'surv-status-banner severed';
      const pct = report.survivability_pct !== undefined ? report.survivability_pct.toFixed(0) : '100';
      survGaugeVal.textContent = `${pct}% ONLINE — NFPA 72 CLASS A`;
      survGaugeSub.textContent = `Fault Isolated: Hub ${report.hub_id} loop split into dual branches`;
      if (survStandardBadge) {
        survStandardBadge.textContent = report.nfpa72_class_a_compliant ? '100% SURVIVABILITY' : 'NON-COMPLIANT';
        survStandardBadge.className = report.nfpa72_class_a_compliant ? 'standard-badge compliant' : 'standard-badge';
      }

      if (survFaultBox) survFaultBox.style.display = 'flex';
      const cutLoc = report.cut_location;
      if (cutLoc) {
        if (faultLeg) faultLeg.textContent = `${cutLoc.from_node} ➔ ${cutLoc.to_node}`;
        const pt = cutLoc.cut_point || {};
        if (faultCoords) faultCoords.textContent = `X: ${(pt.x || 0).toFixed(2)}m, Y: ${(pt.y || 0).toFixed(2)}m`;
      }
      const pNodes = report.primary_branch_nodes || [];
      const rNodes = report.return_branch_nodes || [];
      if (outboundNodesList) {
        outboundNodesList.textContent = pNodes.length > 0 ? pNodes.join(', ') : '(None — Cut at Hub Outbound)';
      }
      if (returnNodesList) {
        returnNodesList.textContent = rNodes.length > 0 ? rNodes.join(', ') : '(None)';
      }
    } else {
      survBanner.className = 'surv-status-banner normal';
      if (survGaugeVal) survGaugeVal.textContent = '100% ONLINE — NFPA 72 CLASS A';
      if (survGaugeSub) survGaugeSub.textContent = 'Closed-Loop Ring Normal (0 Severed Legs)';
      if (survStandardBadge) {
        survStandardBadge.textContent = 'NFPA 72 CLASS A';
        survStandardBadge.className = 'standard-badge compliant';
      }
      if (survFaultBox) survFaultBox.style.display = 'none';
    }
  }

  function updateConnectionUI(status, label) {
    connPill.className = `status-pill ${status}`;
    connStatus.textContent = label;
  }

  // --- Live Reviewer HUD Update ---
  function updateHUD(data) {
    const metrics = data.metrics || {};
    const rules = data.rules_passed || {};
    const violations = data.violations || [];
    const hubs = data.hubs || [];
    const isValid = data.is_valid !== false;

    // Latency
    const lat = data.latency_ms !== undefined ? data.latency_ms : state.measuredLatencyMs;
    latencyReadout.textContent = `${lat.toFixed(1)} ms`;

    // Competition Metrics
    metricJ.textContent = metrics.J !== undefined ? metrics.J.toFixed(2) : '--';
    metricL.textContent = metrics.L !== undefined ? metrics.L.toFixed(2) : '--';
    metricB.textContent = metrics.B !== undefined ? metrics.B : '--';
    metricX.textContent = metrics.X !== undefined ? metrics.X : '--';
    metricO.textContent = metrics.O !== undefined ? metrics.O.toFixed(2) : '--';
    metricH.textContent = metrics.H !== undefined ? metrics.H : '--';
    areaGapVal.textContent = metrics.delta_area_gap_m2 !== undefined ? `${metrics.delta_area_gap_m2.toFixed(2)} m²` : '0.00 m²';

    // Live Equation Breakdown Pills & Penalty Labels
    const pillValL = document.getElementById('pillValL');
    const pillValX = document.getElementById('pillValX');
    const pillValO = document.getElementById('pillValO');
    const pillValB = document.getElementById('pillValB');
    const pillValJ = document.getElementById('pillValJ');
    const subMetricX = document.getElementById('subMetricX');
    const subMetricO = document.getElementById('subMetricO');
    const subMetricB = document.getElementById('subMetricB');

    const valL = metrics.L !== undefined ? Number(metrics.L) : 0;
    const valX = metrics.X !== undefined ? Number(metrics.X) : 0;
    const valO = metrics.O !== undefined ? Number(metrics.O) : 0;
    const valB = metrics.B !== undefined ? Number(metrics.B) : 0;
    const valJ = metrics.J !== undefined ? Number(metrics.J) : (valL + 5 * valX + valO + 0.5 * valB);

    if (pillValL) pillValL.textContent = `${valL.toFixed(2)}m`;
    if (pillValX) pillValX.textContent = `+${(5 * valX).toFixed(2)}`;
    if (pillValO) pillValO.textContent = `+${(1 * valO).toFixed(2)}`;
    if (pillValB) pillValB.textContent = `+${(0.5 * valB).toFixed(2)}`;
    if (pillValJ) pillValJ.textContent = `${valJ.toFixed(2)}`;

    if (subMetricX) subMetricX.textContent = `+${(5 * valX).toFixed(2)} penalty (5x)`;
    if (subMetricO) subMetricO.textContent = `+${(1 * valO).toFixed(2)} penalty (1x)`;
    if (subMetricB) subMetricB.textContent = `+${(0.5 * valB).toFixed(2)} penalty (0.5x)`;

    // Validity Banner
    if (isValid) {
      validityBanner.className = 'validity-banner pass';
      validityTitle.textContent = 'VALID ARCHITECTURAL SOLUTION';
      validitySub.textContent = '100% Slide 3 Hard Rules Passed';
      bannerIconPass.style.display = 'block';
      bannerIconFail.style.display = 'none';
    } else {
      validityBanner.className = 'validity-banner fail';
      validityTitle.textContent = 'RULE VIOLATIONS DETECTED';
      validitySub.textContent = `${violations.length} Critical Issue(s) Require Correction`;
      bannerIconPass.style.display = 'none';
      bannerIconFail.style.display = 'block';
    }

    // Slide 3 Checklist
    let passedCount = 0;
    for (let i = 1; i <= 6; i++) {
      const rKey = `Rule ${i}`;
      const isPass = rules[rKey] === true;
      if (isPass) passedCount++;

      const badge = document.getElementById(`badgeRule${i}`);
      const row = document.getElementById(`ruleRow${i}`);

      if (badge && row) {
        badge.className = `rule-status-badge ${isPass ? 'pass' : 'fail'}`;
        badge.textContent = isPass ? '[PASS]' : '[FAIL]';
        row.className = `rule-row ${isPass ? '' : 'violation'}`;
      }
    }
    rulesScoreBadge.textContent = `${passedCount} / 6 PASSED`;
    rulesScoreBadge.style.color = passedCount === 6 ? 'var(--color-pass)' : 'var(--color-fail)';
    rulesScoreBadge.style.borderColor = passedCount === 6 ? 'var(--color-pass)' : 'var(--color-fail)';

    // Diagnostics Box
    if (violations.length > 0) {
      diagnosticsBox.style.display = 'block';
      diagnosticsList.innerHTML = violations
        .map(v => `<li><strong>[Rule ${v.rule_number}] ${escapeHtml(v.rule_name)}:</strong> ${escapeHtml(v.message)}</li>`)
        .join('');
    } else {
      diagnosticsBox.style.display = 'none';
      diagnosticsList.innerHTML = '';
    }

    // Per-Hub Cards
    renderHubCards(hubs, data.routes || []);
  }

  function renderHubCards(hubs, routes) {
    if (!hubs || hubs.length === 0) {
      hubsContainer.innerHTML = '<div style="color: var(--text-dim); font-size: 11px;">No hubs active</div>';
      return;
    }

    const routeMap = {};
    routes.forEach(r => { routeMap[r.hub_id] = r; });

    hubsContainer.innerHTML = hubs.map((hub, idx) => {
      const r = routeMap[hub.hub_id] || {};
      const ringLen = r.ring_length_m !== undefined ? r.ring_length_m.toFixed(2) : '0.00';
      const bends = r.ring_bends !== undefined ? r.ring_bends : 0;
      const nodesCount = (hub.assigned_nodes || []).length;
      const area = (hub.zone_area_m2 || 0).toFixed(1);
      const color = CONFIG.hubColors[idx % CONFIG.hubColors.length];

      return `
        <div class="hub-card" style="border-left-color: ${color}">
          <div class="hub-card-header">
            <span style="color: ${color}">Hub ${escapeHtml(hub.hub_id)} (${escapeHtml(hub.room_id)})</span>
            <span>${ringLen} m</span>
          </div>
          <div class="hub-card-stats">
            <div>Area: ${area} m²</div>
            <div>Nodes: ${nodesCount}</div>
            <div>Bends: ${bends}</div>
          </div>
        </div>
      `;
    }).join('');
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  // --- High-Performance 60 FPS Canvas Rendering Engine ---
  function resizeCanvas() {
    const rect = canvasWrapper.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function renderLoop(time) {
    // FPS calculation
    state.frameCount++;
    if (time - state.lastFpsTime >= 1000) {
      state.currentFps = Math.round((state.frameCount * 1000) / (time - state.lastFpsTime));
      fpsReadout.textContent = `${state.currentFps} FPS`;
      state.frameCount = 0;
      state.lastFpsTime = time;
    }

    drawScene();
    render3D();
    requestAnimationFrame(renderLoop);
  }

  function drawScene() {
    const width = canvas.width / window.devicePixelRatio;
    const height = canvas.height / window.devicePixelRatio;

    // Clear canvas
    ctx.fillStyle = '#F8FAFC'; // Clean architectural drafting backdrop
    ctx.fillRect(0, 0, width, height);

    // Subtle background grid
    drawGrid(width, height);

    if (!state.serverState) {
      ctx.fillStyle = '#64748B';
      ctx.font = '14px Plus Jakarta Sans, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Connecting to ResPlan engine...', width / 2, height / 2);
      return;
    }

    const plan = state.serverState.plan || {};

    // 1. Draw Rooms
    drawRooms(plan.rooms || []);

    // 2. Draw Obstacles (Walls & Windows)
    drawObstacles(plan.obstacles || []);

    // 3. Draw Halos
    if (state.showHalos) {
      drawHalos();
    }

    // Advance 60 FPS pulse animation
    state.pulsePhase = (state.pulsePhase + 0.3) % 30;

    // 4. Draw Cable Routes
    drawRoutes(state.serverState.routes || []);

    // 5. Draw Hubs
    drawHubs(state.serverState.hubs || []);

    // 6. Draw Fixed Nodes
    drawNodes(state.serverState.nodes || []);

    // 7. Draw Ghost Doorways (Co-Pilot Recommendations)
    if (state.showGhostDoors && state.copilotProposals && state.copilotProposals.length > 0) {
      drawGhostDoorways(state.copilotProposals);
    }
  }

  function drawGrid(w, h) {
    ctx.save();
    ctx.strokeStyle = '#E2E8F0';
    ctx.lineWidth = 1;
    const step = state.transform.scale; // 1 metre grid

    if (step > 10) {
      const startX = state.transform.offsetX % step;
      const startY = (h - state.transform.offsetY) % step;

      ctx.beginPath();
      for (let x = startX; x < w; x += step) {
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
      }
      for (let y = startY; y < h; y += step) {
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawRooms(rooms) {
    ctx.save();
    rooms.forEach(r => {
      const poly = r.polygon || [];
      if (poly.length < 3) return;

      const cat = (r.category || '').toLowerCase();
      const fillColor = CONFIG.roomPastelPalette[cat] || CONFIG.roomPastelPalette.default;

      ctx.beginPath();
      const p0 = worldToScreen(poly[0][0], poly[0][1]);
      ctx.moveTo(p0.x, p0.y);
      for (let i = 1; i < poly.length; i++) {
        const pt = worldToScreen(poly[i][0], poly[i][1]);
        ctx.lineTo(pt.x, pt.y);
      }
      ctx.closePath();

      ctx.fillStyle = fillColor;
      ctx.fill();
      ctx.strokeStyle = '#CBD5E1';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Room label
      const cx = poly.reduce((acc, p) => acc + p[0], 0) / poly.length;
      const cy = poly.reduce((acc, p) => acc + p[1], 0) / poly.length;
      const centerScreen = worldToScreen(cx, cy);

      ctx.fillStyle = '#475569';
      ctx.font = '600 11px Plus Jakarta Sans, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(r.room_id || '', centerScreen.x, centerScreen.y - 6);

      ctx.fillStyle = '#94A3B8';
      ctx.font = '9px JetBrains Mono, monospace';
      ctx.fillText(`${(r.area_m2 || 0).toFixed(1)} m²`, centerScreen.x, centerScreen.y + 8);
    });
    ctx.restore();
  }

  function drawObstacles(obstacles) {
    ctx.save();
    ctx.fillStyle = '#1E293B';
    ctx.strokeStyle = '#0F172A';
    ctx.lineWidth = 1;

    obstacles.forEach(poly => {
      if (!poly || poly.length < 3) return;
      ctx.beginPath();
      const p0 = worldToScreen(poly[0][0], poly[0][1]);
      ctx.moveTo(p0.x, p0.y);
      for (let i = 1; i < poly.length; i++) {
        const pt = worldToScreen(poly[i][0], poly[i][1]);
        ctx.lineTo(pt.x, pt.y);
      }
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    });
    ctx.restore();
  }

  function drawHalos() {
    const halos = (state.serverState && state.serverState.halos) || {};
    const radiusPixels = CONFIG.haloRadiusM * state.transform.scale;

    // Draw halos for all entities
    Object.keys(halos).forEach(id => {
      const hInfo = halos[id];
      const isTarget = state.dragTarget && state.dragTarget.id === id;
      let wx, wy;

      if (isTarget) {
        wx = state.dragTarget.currentX;
        wy = state.dragTarget.currentY;
      } else {
        const node = (state.serverState.nodes || []).find(n => n.node_id === id);
        const hub = (state.serverState.hubs || []).find(h => h.hub_id === id);
        if (node) { wx = node.x; wy = node.y; }
        else if (hub) { wx = hub.x; wy = hub.y; }
        else return;
      }

      const scr = worldToScreen(wx, wy);
      const isClear = hInfo.is_clear;

      ctx.save();
      ctx.beginPath();
      ctx.arc(scr.x, scr.y, radiusPixels, 0, Math.PI * 2);

      if (isClear) {
        ctx.fillStyle = CONFIG.haloSafeColor;
        ctx.strokeStyle = CONFIG.haloSafeStroke;
      } else {
        ctx.fillStyle = CONFIG.haloViolColor;
        ctx.strokeStyle = CONFIG.haloViolStroke;
      }

      ctx.lineWidth = 2;
      ctx.fill();
      ctx.stroke();

      // Show distance badge if violating
      if (!isClear || isTarget) {
        ctx.font = '10px JetBrains Mono, monospace';
        ctx.fillStyle = isClear ? '#065F46' : '#991B1B';
        ctx.textAlign = 'center';
        ctx.fillText(`${(hInfo.distance_m || 0).toFixed(2)}m`, scr.x, scr.y - radiusPixels - 6);
      }

      ctx.restore();
    });
  }

  function drawRoutes(routes) {
    if (!routes) return;

    routes.forEach((ring, idx) => {
      const ringColor = CONFIG.hubColors[idx % CONFIG.hubColors.length];
      const segments = ring.segments || [];
      const isRingCut = state.activeCutReport && state.activeCutReport.is_severed && state.activeCutReport.hub_id === ring.hub_id;
      const cutLoc = isRingCut ? state.activeCutReport.cut_location : null;

      segments.forEach((seg, sIdx) => {
        const pts = seg.polyline || [];
        if (pts.length < 2) return;

        const isHovered = state.scissorsMode && state.hoveredCable &&
                          state.hoveredCable.ringIdx === idx && state.hoveredCable.segIdx === sIdx;

        ctx.save();

        if (isRingCut && cutLoc) {
          const cutLeg = cutLoc.leg_index;
          let legColor = ringColor;
          let isPulseOutbound = false;
          let isPulseReturn = false;

          if (sIdx < cutLeg) {
            legColor = '#00D2FF';
            isPulseOutbound = true;
          } else if (sIdx > cutLeg) {
            legColor = '#FFA500';
            isPulseReturn = true;
          } else {
            legColor = '#64748B';
          }

          // Base polyline
          ctx.strokeStyle = isHovered ? '#FF007F' : legColor;
          ctx.lineWidth = isHovered ? 5.0 : 3.5;
          ctx.lineCap = 'round';
          ctx.lineJoin = 'round';

          ctx.beginPath();
          const p0 = worldToScreen(pts[0][0], pts[0][1]);
          ctx.moveTo(p0.x, p0.y);
          for (let i = 1; i < pts.length; i++) {
            const pt = worldToScreen(pts[i][0], pts[i][1]);
            ctx.lineTo(pt.x, pt.y);
          }
          ctx.stroke();

          // Dual-direction animated pulses
          if (isPulseOutbound) {
            ctx.strokeStyle = '#00D2FF';
            ctx.lineWidth = 4.5;
            ctx.setLineDash([8, 8]);
            ctx.lineDashOffset = -state.pulsePhase * 2.5;
            ctx.stroke();
          } else if (isPulseReturn) {
            ctx.strokeStyle = '#FFA500';
            ctx.lineWidth = 4.5;
            ctx.setLineDash([8, 8]);
            ctx.lineDashOffset = state.pulsePhase * 2.5;
            ctx.stroke();
          } else if (sIdx === cutLeg && cutLoc.cut_point) {
            const cp = cutLoc.cut_point;
            const pStart = worldToScreen(pts[0][0], pts[0][1]);
            const pCut = worldToScreen(cp.x, cp.y);
            const pEnd = worldToScreen(pts[pts.length - 1][0], pts[pts.length - 1][1]);

            // Upstream portion: cyan pulse flowing outward to cut
            ctx.beginPath();
            ctx.moveTo(pStart.x, pStart.y);
            ctx.lineTo(pCut.x, pCut.y);
            ctx.strokeStyle = '#00D2FF';
            ctx.lineWidth = 4.5;
            ctx.setLineDash([8, 8]);
            ctx.lineDashOffset = -state.pulsePhase * 2.5;
            ctx.stroke();

            // Downstream portion: amber pulse flowing reverse to cut
            ctx.beginPath();
            ctx.moveTo(pEnd.x, pEnd.y);
            ctx.lineTo(pCut.x, pCut.y);
            ctx.strokeStyle = '#FFA500';
            ctx.lineWidth = 4.5;
            ctx.setLineDash([8, 8]);
            ctx.lineDashOffset = -state.pulsePhase * 2.5;
            ctx.stroke();
          }
        } else {
          // Normal closed loop
          ctx.strokeStyle = isHovered ? '#FF007F' : ringColor;
          ctx.lineWidth = isHovered ? 5.0 : 3.5;
          ctx.lineCap = 'round';
          ctx.lineJoin = 'round';

          ctx.beginPath();
          const p0 = worldToScreen(pts[0][0], pts[0][1]);
          ctx.moveTo(p0.x, p0.y);
          for (let i = 1; i < pts.length; i++) {
            const pt = worldToScreen(pts[i][0], pts[i][1]);
            ctx.lineTo(pt.x, pt.y);
          }
          ctx.stroke();

          if (state.showBadges && pts.length >= 2) {
            const midIdx = Math.floor(pts.length / 2);
            const pA = worldToScreen(pts[midIdx - 1][0], pts[midIdx - 1][1]);
            const pB = worldToScreen(pts[midIdx][0], pts[midIdx][1]);
            drawFlowArrow(pA, pB, isHovered ? '#FF007F' : ringColor);
          }
        }

        ctx.restore();
      });

      // Render severed cut marker on canvas
      if (isRingCut && cutLoc && cutLoc.cut_point) {
        drawCutMarker(cutLoc.cut_point);
      }
    });

    // If scissors tool is active and hovering a cable, draw scissors target cursor
    if (state.scissorsMode && state.hoveredCable && state.hoveredCable.cutPoint) {
      drawScissorsCursor(state.hoveredCable.cutPoint);
    }
  }

  function drawCutMarker(pt) {
    const scr = worldToScreen(pt.x, pt.y);
    ctx.save();

    // Red spark pulse halo
    const pulseRad = 15 + Math.sin(state.pulsePhase * 0.5) * 4;
    ctx.beginPath();
    ctx.arc(scr.x, scr.y, pulseRad, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(230, 25, 75, 0.35)';
    ctx.fill();
    ctx.strokeStyle = '#E6194B';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Sever break symbol
    ctx.fillStyle = '#FFFFFF';
    ctx.font = 'bold 15px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('✂️', scr.x, scr.y);

    ctx.restore();
  }

  function drawScissorsCursor(pt) {
    const scr = worldToScreen(pt.x, pt.y);
    ctx.save();
    ctx.beginPath();
    ctx.arc(scr.x, scr.y, 10, 0, Math.PI * 2);
    ctx.strokeStyle = '#FF007F';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.stroke();

    ctx.fillStyle = '#FF007F';
    ctx.font = '12px sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('✂️', scr.x, scr.y);
    ctx.restore();
  }

  function drawFlowArrow(fromPt, toPt, color) {
    const angle = Math.atan2(toPt.y - fromPt.y, toPt.x - fromPt.x);
    const midX = (fromPt.x + toPt.x) / 2;
    const midY = (fromPt.y + toPt.y) / 2;
    const arrowLen = 8;

    ctx.save();
    ctx.translate(midX, midY);
    ctx.rotate(angle);
    ctx.fillStyle = color;

    ctx.beginPath();
    ctx.moveTo(arrowLen, 0);
    ctx.lineTo(-arrowLen, -arrowLen * 0.6);
    ctx.lineTo(-arrowLen * 0.5, 0);
    ctx.lineTo(-arrowLen, arrowLen * 0.6);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }

  function drawHubs(hubs) {
    hubs.forEach((hub, idx) => {
      const isDragged = state.dragTarget && state.dragTarget.type === 'hub' && state.dragTarget.id === hub.hub_id;
      const wx = isDragged ? state.dragTarget.currentX : hub.x;
      const wy = isDragged ? state.dragTarget.currentY : hub.y;
      const scr = worldToScreen(wx, wy);
      const ringColor = CONFIG.hubColors[idx % CONFIG.hubColors.length];

      ctx.save();
      // Outer glow if dragged
      if (isDragged) {
        ctx.shadowColor = ringColor;
        ctx.shadowBlur = 12;
      }

      // Hub rounded hexagon / badge
      ctx.fillStyle = ringColor;
      ctx.strokeStyle = '#FFFFFF';
      ctx.lineWidth = 2.5;

      const size = 16;
      ctx.beginPath();
      ctx.roundRect ? ctx.roundRect(scr.x - size, scr.y - size, size * 2, size * 2, 6) : ctx.rect(scr.x - size, scr.y - size, size * 2, size * 2);
      ctx.fill();
      ctx.stroke();

      // Label
      ctx.fillStyle = '#FFFFFF';
      ctx.font = '700 11px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(hub.hub_id, scr.x, scr.y);

      ctx.restore();
    });
  }

  function drawNodes(nodes) {
    nodes.forEach(node => {
      const isDragged = state.dragTarget && state.dragTarget.type === 'node' && state.dragTarget.id === node.node_id;
      const wx = isDragged ? state.dragTarget.currentX : node.x;
      const wy = isDragged ? state.dragTarget.currentY : node.y;
      const scr = worldToScreen(wx, wy);

      ctx.save();
      if (isDragged) {
        ctx.shadowColor = '#0284C7';
        ctx.shadowBlur = 10;
      }

      // Check survivability status if cut is active
      let nodeBorderColor = '#38BDF8';
      let statusTag = null;
      let statusColor = null;

      if (state.activeCutReport && state.activeCutReport.node_statuses) {
        const nStat = state.activeCutReport.node_statuses[node.node_id];
        if (nStat) {
          if (nStat.power_source === 'primary_outbound') {
            nodeBorderColor = '#00D2FF';
            statusTag = '⚡ OUT';
            statusColor = '#00D2FF';
          } else if (nStat.power_source === 'return_terminal') {
            nodeBorderColor = '#FFA500';
            statusTag = '⚡ RET';
            statusColor = '#FFA500';
          } else {
            nodeBorderColor = '#E6194B';
            statusTag = 'OFF';
            statusColor = '#E6194B';
          }

          // Outer survivability halo
          ctx.beginPath();
          ctx.arc(scr.x, scr.y, 16, 0, Math.PI * 2);
          ctx.strokeStyle = statusColor;
          ctx.lineWidth = 2.0;
          ctx.stroke();
        }
      }

      // Circle badge
      ctx.beginPath();
      ctx.arc(scr.x, scr.y, 11, 0, Math.PI * 2);
      ctx.fillStyle = '#0F172A';
      ctx.fill();
      ctx.strokeStyle = nodeBorderColor;
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#FFFFFF';
      ctx.font = '700 9px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.node_id, scr.x, scr.y);

      // Status tag badge above node
      if (statusTag && statusColor) {
        ctx.fillStyle = statusColor;
        ctx.font = '700 8px JetBrains Mono, monospace';
        ctx.fillText(statusTag, scr.x, scr.y - 18);
      }

      ctx.restore();
    });
  }

  // --- Ghost Doorways Drawing (Phase 8 Co-Pilot) ---
  function drawGhostDoorways(proposals) {
    if (!proposals || !proposals.length) return;

    proposals.forEach((prop, idx) => {
      const wallSeg = prop.wall_segment;
      if (!wallSeg || wallSeg.length < 2) return;

      const p1 = wallSeg[0];
      const p2 = wallSeg[1];
      const dx = p2.x - p1.x;
      const dy = p2.y - p1.y;
      const segLen = Math.hypot(dx, dy);
      const tx = segLen > 1e-6 ? dx / segLen : 1;
      const ty = segLen > 1e-6 ? dy / segLen : 0;
      const hw = (prop.door_width_m || 0.90) / 2.0;

      const cx = prop.suggested_door_center.x;
      const cy = prop.suggested_door_center.y;

      const startPt = { x: cx - hw * tx, y: cy - hw * ty };
      const endPt = { x: cx + hw * tx, y: cy + hw * ty };

      const sStart = worldToScreen(startPt.x, startPt.y);
      const sEnd = worldToScreen(endPt.x, endPt.y);
      const sCenter = worldToScreen(cx, cy);

      // 1. Draw glowing dashed cyan doorway opening
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(sStart.x, sStart.y);
      ctx.lineTo(sEnd.x, sEnd.y);
      ctx.lineWidth = Math.max(5, 7 * (state.transform.scale / 40));
      ctx.strokeStyle = '#00D2FF';
      ctx.setLineDash([8, 5]);
      ctx.lineCap = 'round';
      ctx.shadowColor = 'rgba(0, 210, 255, 0.75)';
      ctx.shadowBlur = 12;
      ctx.stroke();

      // Door threshold end ticks
      ctx.setLineDash([]);
      ctx.lineWidth = 2;
      const nx = -ty * 6;
      const ny = tx * 6;
      ctx.beginPath();
      ctx.moveTo(sStart.x - nx, sStart.y - ny);
      ctx.lineTo(sStart.x + nx, sStart.y + ny);
      ctx.moveTo(sEnd.x - nx, sEnd.y - ny);
      ctx.lineTo(sEnd.x + nx, sEnd.y + ny);
      ctx.stroke();
      ctx.restore();

      // 2. Floating savings pill badge directly above doorway center
      const labelText = prop.delta_hubs > 0
        ? `Save ${prop.delta_length_m.toFixed(1)}m (ΔH = -${prop.delta_hubs})`
        : `Save ${prop.delta_length_m.toFixed(1)}m (ΔJ = -${prop.delta_cost_J.toFixed(1)})`;

      ctx.save();
      ctx.font = 'bold 11px JetBrains Mono, monospace';
      const textMetrics = ctx.measureText(labelText);
      const paddingX = 8;
      const pillHeight = 20;
      const pillWidth = textMetrics.width + paddingX * 2;
      const pillX = sCenter.x - pillWidth / 2;
      const pillY = sCenter.y - 24;

      // Glow & background
      ctx.fillStyle = 'rgba(0, 210, 255, 0.95)';
      ctx.shadowColor = 'rgba(0, 0, 0, 0.35)';
      ctx.shadowBlur = 6;
      ctx.shadowOffsetY = 2;
      ctx.beginPath();
      if (ctx.roundRect) {
        ctx.roundRect(pillX, pillY, pillWidth, pillHeight, 10);
      } else {
        ctx.rect(pillX, pillY, pillWidth, pillHeight);
      }
      ctx.fill();

      // Pill label text
      ctx.fillStyle = '#0F172A';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(labelText, sCenter.x, pillY + pillHeight / 2);
      ctx.restore();
    });
  }

  // --- Pointer & Hit-Testing Interaction ---
  function getPointerCanvasPos(e) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: e.clientX - rect.left,
      y: e.clientY - rect.top,
    };
  }

  function findHitEntity(screenX, screenY) {
    if (!state.serverState) return null;

    // Check hubs first (radius ~18px)
    const hubs = state.serverState.hubs || [];
    for (let i = 0; i < hubs.length; i++) {
      const h = hubs[i];
      const scr = worldToScreen(h.x, h.y);
      const dist = Math.hypot(screenX - scr.x, screenY - scr.y);
      if (dist <= 20) {
        return { type: 'hub', id: h.hub_id, x: h.x, y: h.y };
      }
    }

    // Check nodes (radius ~15px)
    const nodes = state.serverState.nodes || [];
    for (let i = 0; i < nodes.length; i++) {
      const n = nodes[i];
      const scr = worldToScreen(n.x, n.y);
      const dist = Math.hypot(screenX - scr.x, screenY - scr.y);
      if (dist <= 16) {
        return { type: 'node', id: n.node_id, x: n.x, y: n.y };
      }
    }

    return null;
  }

  // Mouse / Touch Event Handlers
  canvas.addEventListener('mousedown', function (e) {
    const pos = getPointerCanvasPos(e);

    // Pan with middle mouse or shift+left click
    if (e.button === 1 || (e.button === 0 && e.shiftKey)) {
      state.isPanning = true;
      state.panStart = { x: e.clientX - state.transform.offsetX, y: e.clientY - state.transform.offsetY };
      canvas.style.cursor = 'grabbing';
      return;
    }

    if (e.button === 0) {
      if (state.scissorsMode) {
        const worldPos = screenToWorld(pos.x, pos.y);
        const cableHit = findNearestCablePoint(worldPos.x, worldPos.y, 0.45);
        if (cableHit) {
          sendWebSocketMessage({
            action: 'wire_cut',
            hub_id: cableHit.hubId,
            cut_x: Math.round(cableHit.cutPoint.x * 100) / 100,
            cut_y: Math.round(cableHit.cutPoint.y * 100) / 100,
          });
          return;
        }
      }

      const hit = findHitEntity(pos.x, pos.y);
      if (hit) {
        state.dragTarget = {
          type: hit.type,
          id: hit.id,
          initialX: hit.x,
          initialY: hit.y,
          currentX: hit.x,
          currentY: hit.y,
        };
        canvas.style.cursor = 'grab';
      }
    }
  });

  window.addEventListener('mousemove', function (e) {
    const pos = getPointerCanvasPos(e);
    const worldPos = screenToWorld(pos.x, pos.y);
    state.mouseWorldPos = worldPos;

    coordDisplay.textContent = `X: ${worldPos.x.toFixed(2)}m | Y: ${worldPos.y.toFixed(2)}m`;

    if (state.isPanning) {
      state.transform.offsetX = e.clientX - state.panStart.x;
      state.transform.offsetY = e.clientY - state.panStart.y;
      return;
    }

    if (state.scissorsMode) {
      state.hoveredCable = findNearestCablePoint(worldPos.x, worldPos.y, 0.45);
      canvas.style.cursor = 'crosshair';
    } else if (state.dragTarget) {
      canvas.style.cursor = 'grabbing';
      state.dragTarget.currentX = worldPos.x;
      state.dragTarget.currentY = worldPos.y;
      update3DDragMesh(state.dragTarget);

      // Throttle sending updates to server via requestAnimationFrame
      if (!state.inFlightAction) {
        if (state.dragTarget.type === 'node') {
          sendWebSocketMessage({
            action: 'drag_node',
            node_id: state.dragTarget.id,
            x: Math.round(worldPos.x * 100) / 100,
            y: Math.round(worldPos.y * 100) / 100,
          });
        } else if (state.dragTarget.type === 'hub') {
          sendWebSocketMessage({
            action: 'drag_hub',
            hub_id: state.dragTarget.id,
            x: Math.round(worldPos.x * 100) / 100,
            y: Math.round(worldPos.y * 100) / 100,
          });
        }
      }
    } else {
      state.hoveredCable = null;
      // Hover cursor
      const hit = findHitEntity(pos.x, pos.y);
      canvas.style.cursor = hit ? 'pointer' : 'default';
    }
  });

  window.addEventListener('mouseup', function () {
    if (state.isPanning) {
      state.isPanning = false;
      canvas.style.cursor = state.scissorsMode ? 'crosshair' : 'default';
    }
    if (state.dragTarget) {
      // Send final position commit
      if (state.dragTarget.type === 'node') {
        sendWebSocketMessage({
          action: 'drag_node',
          node_id: state.dragTarget.id,
          x: Math.round(state.dragTarget.currentX * 100) / 100,
          y: Math.round(state.dragTarget.currentY * 100) / 100,
        });
      } else if (state.dragTarget.type === 'hub') {
        sendWebSocketMessage({
          action: 'drag_hub',
          hub_id: state.dragTarget.id,
          x: Math.round(state.dragTarget.currentX * 100) / 100,
          y: Math.round(state.dragTarget.currentY * 100) / 100,
        });
      }
      reset3DDragMesh(state.dragTarget);
      state.dragTarget = null;
      canvas.style.cursor = 'crosshair';
    }
  });

  canvas.addEventListener('wheel', function (e) {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.15 : 0.85;
    const pos = getPointerCanvasPos(e);
    const worldBefore = screenToWorld(pos.x, pos.y);

    state.transform.scale = Math.max(10, Math.min(200, state.transform.scale * zoomFactor));

    // Re-adjust offset to keep world point under mouse fixed
    const worldAfter = screenToWorld(pos.x, pos.y);
    state.transform.offsetX += (worldAfter.x - worldBefore.x) * state.transform.scale;
    state.transform.offsetY += (worldAfter.y - worldBefore.y) * state.transform.scale;
  }, { passive: false });

  // --- Toolbar Controls ---
  btnZoomIn.addEventListener('click', () => {
    state.transform.scale *= 1.25;
  });

  btnZoomOut.addEventListener('click', () => {
    state.transform.scale = Math.max(10, state.transform.scale * 0.8);
  });

  btnFitScreen.addEventListener('click', fitToScreen);
  btnResetView.addEventListener('click', fitToScreen);

  chkHalos.addEventListener('change', (e) => {
    state.showHalos = e.target.checked;
  });

  chkBadges.addEventListener('change', (e) => {
    state.showBadges = e.target.checked;
  });

  // --- Co-Pilot Architectural Suggestions (Phase 8) ---
  async function requestCopilotSuggestions() {
    state.copilotLoading = true;
    if (copilotContainer) {
      copilotContainer.innerHTML = `
        <div class="copilot-empty-state">
          <span>Exploring layout perturbations and calculating rectilinear cost deltas...</span>
        </div>
      `;
    }

    if (state.wsConnected && state.ws && state.ws.readyState === WebSocket.OPEN) {
      sendWebSocketMessage({ action: 'copilot_suggest', max_proposals: 5 });
    } else {
      try {
        const resp = await fetch(`${CONFIG.httpFallbackBase}/api/copilot/suggest`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ max_proposals: 5 }),
        });
        if (resp.ok) {
          const report = await resp.json();
          state.copilotLoading = false;
          state.copilotProposals = report.recommended_proposals || [];
          renderCopilotProposals(state.copilotProposals, report);
        }
      } catch (err) {
        console.error('Co-Pilot suggest failed:', err);
        state.copilotLoading = false;
        if (copilotContainer) {
          copilotContainer.innerHTML = `
            <div class="copilot-empty-state">
              <span>Could not generate proposals: ${escapeHtml(err.message)}</span>
            </div>
          `;
        }
      }
    }
  }

  async function applyCopilotProposal(proposalId) {
    state.activeProposalId = proposalId;
    if (copilotContainer) {
      const btn = copilotContainer.querySelector(`button[data-id="${proposalId}"]`);
      if (btn) {
        btn.disabled = true;
        btn.textContent = 'Applying...';
      }
    }

    if (state.wsConnected && state.ws && state.ws.readyState === WebSocket.OPEN) {
      sendWebSocketMessage({ action: 'copilot_apply', proposal_id: proposalId });
    } else {
      try {
        const resp = await fetch(`${CONFIG.httpFallbackBase}/api/copilot/apply`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ proposal_id: proposalId }),
        });
        if (resp.ok) {
          const newState = await resp.json();
          handleServerPayload(newState);
        }
      } catch (err) {
        console.error('Co-Pilot apply failed:', err);
        state.activeProposalId = null;
      }
    }
  }

  function renderCopilotProposals(proposals, report) {
    if (!copilotContainer) return;

    if (!proposals || proposals.length === 0) {
      copilotContainer.innerHTML = `
        <div class="copilot-empty-state">
          <span>Click "Suggest Improvements" to analyze layout perturbations.</span>
        </div>
      `;
      return;
    }

    let html = '';
    proposals.forEach((prop, idx) => {
      const isSlide = prop.proposal_type === 'slide_door';
      const title = isSlide ? `Slide Doorway` : `Ghost Doorway`;
      const roomTags = `<span class="proposal-room-tag">${escapeHtml(prop.room_a_id)} ↔ ${escapeHtml(prop.room_b_id)}</span>`;

      let chips = '';
      if (prop.delta_hubs > 0) {
        chips += `<span class="impact-chip hub">ΔH: -${prop.delta_hubs}</span>`;
      }
      chips += `<span class="impact-chip cost">ΔJ: -${prop.delta_cost_J.toFixed(1)}</span>`;
      chips += `<span class="impact-chip length">ΔL: -${prop.delta_length_m.toFixed(1)}m</span>`;
      chips += `<span class="impact-chip bend">ΔB: -${prop.delta_bends}</span>`;

      html += `
        <div class="copilot-proposal-card" data-id="${escapeHtml(prop.proposal_id)}">
          <div class="proposal-card-header">
            <div class="proposal-title">
              <span>#${idx + 1} ${title}</span>
              ${roomTags}
            </div>
          </div>
          <div class="proposal-chips">
            ${chips}
          </div>
          <div class="proposal-rationale">
            ${escapeHtml(prop.rationale)}
          </div>
          <button class="btn-apply-tweak" data-id="${escapeHtml(prop.proposal_id)}">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <polyline points="20 6 9 17 4 12"/>
            </svg>
            Apply Tweak
          </button>
        </div>
      `;
    });

    copilotContainer.innerHTML = html;

    // Attach click events to Apply Tweak buttons
    copilotContainer.querySelectorAll('.btn-apply-tweak').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.getAttribute('data-id');
        if (id) {
          applyCopilotProposal(id);
        }
      });
    });
  }

  // --- Toolbar & Action Listeners for Co-Pilot ---
  if (chkGhostDoors) {
    chkGhostDoors.addEventListener('change', (e) => {
      state.showGhostDoors = e.target.checked;
    });
  }

  if (btnSuggestToolbar) {
    btnSuggestToolbar.addEventListener('click', requestCopilotSuggestions);
  }

  if (btnSuggestActions) {
    btnSuggestActions.addEventListener('click', requestCopilotSuggestions);
  }

  if (btnRefreshCopilot) {
    btnRefreshCopilot.addEventListener('click', requestCopilotSuggestions);
  }

  // --- Wire-Cut Survivability Scissors & Heal Listeners ---
  if (btnScissorsTool) {
    btnScissorsTool.addEventListener('click', () => {
      state.scissorsMode = !state.scissorsMode;
      btnScissorsTool.classList.toggle('active', state.scissorsMode);
      canvasWrapper.classList.toggle('scissors-active', state.scissorsMode);
      canvas.style.cursor = state.scissorsMode ? 'crosshair' : 'default';
    });
  }

  if (btnHealCircuit) {
    btnHealCircuit.addEventListener('click', () => {
      sendWebSocketMessage({ action: 'wire_heal' });
    });
  }

  // --- Action Buttons ---
  btnResetBaseline.addEventListener('click', async () => {
    sendWebSocketMessage({ action: 'reset' });
  });

  btnAutoOptimize.addEventListener('click', async () => {
    sendWebSocketMessage({ action: 'reset' });
  });

  btnExportSlide8.addEventListener('click', async () => {
    try {
      btnExportSlide8.disabled = true;
      btnExportSlide8.textContent = 'Exporting...';

      const res = await fetch(`${CONFIG.httpFallbackBase}/api/export`, { method: 'POST' });
      if (!res.ok) throw new Error(`Export failed with status ${res.status}`);

      const data = await res.json();
      const jsonStr = JSON.stringify(data, null, 2);
      const blob = new Blob([jsonStr], { type: 'application/json' });
      const url = URL.createObjectURL(blob);

      const a = document.createElement('a');
      a.href = url;
      a.download = `resplan_${data.plan_id || 'slide8'}_solution.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      alert(`Export error: ${err.message}`);
    } finally {
      btnExportSlide8.disabled = false;
      btnExportSlide8.innerHTML = `
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
        </svg>
        Export Slide 8 JSON
      `;
    }
  });

  // =========================================================================
  // Three.js 3D BIM Architectural Engine (Phase 10)
  // =========================================================================

  function initThreeEngine() {
    if (typeof THREE === 'undefined') {
      console.warn('Three.js not loaded. Skipping 3D BIM initialization.');
      return;
    }
    const container = document.getElementById('bimViewport');
    if (!container || bim3d.isInitialized) return;

    bim3d.container = container;
    const width = container.clientWidth || 600;
    const height = container.clientHeight || 500;

    // WebGL Renderer
    bim3d.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    bim3d.renderer.setSize(width, height);
    bim3d.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    bim3d.renderer.shadowMap.enabled = true;
    bim3d.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    if (THREE.sRGBEncoding) bim3d.renderer.outputEncoding = THREE.sRGBEncoding;

    container.innerHTML = '';
    container.appendChild(bim3d.renderer.domElement);

    // Scene
    bim3d.scene = new THREE.Scene();
    bim3d.scene.background = new THREE.Color(0x0f172a); // dark architectural navy

    // Perspective Camera
    bim3d.camera = new THREE.PerspectiveCamera(45, width / (height || 1), 0.1, 500);
    bim3d.camera.position.set(18, 16, 20);

    // Orbit Controls
    if (typeof THREE.OrbitControls !== 'undefined') {
      bim3d.controls = new THREE.OrbitControls(bim3d.camera, bim3d.renderer.domElement);
      bim3d.controls.enableDamping = true;
      bim3d.controls.dampingFactor = 0.08;
      bim3d.controls.maxPolarAngle = Math.PI / 2 - 0.02; // Prevent camera going below slab
      bim3d.controls.target.set(7.0, 1.35, 4.5);
      bim3d.controls.update();
    }

    // Directional Architectural Sunlight & Fill
    bim3d.ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
    bim3d.scene.add(bim3d.ambientLight);

    bim3d.dirLight = new THREE.DirectionalLight(0xfff8e7, 0.85);
    bim3d.dirLight.position.set(18, 25, 20);
    bim3d.dirLight.castShadow = true;
    bim3d.dirLight.shadow.mapSize.width = 2048;
    bim3d.dirLight.shadow.mapSize.height = 2048;
    bim3d.dirLight.shadow.camera.near = 0.5;
    bim3d.dirLight.shadow.camera.far = 100;
    const d = 16;
    bim3d.dirLight.shadow.camera.left = -d;
    bim3d.dirLight.shadow.camera.right = d;
    bim3d.dirLight.shadow.camera.top = d;
    bim3d.dirLight.shadow.camera.bottom = -d;
    bim3d.scene.add(bim3d.dirLight);

    const fillLight = new THREE.DirectionalLight(0x38bdf8, 0.35);
    fillLight.position.set(-15, 12, -12);
    bim3d.scene.add(fillLight);

    // Grid Helper at Y=0
    bim3d.gridHelper = new THREE.GridHelper(30, 30, 0x334155, 0x1e293b);
    bim3d.gridHelper.position.set(7.0, -0.01, 4.5);
    bim3d.scene.add(bim3d.gridHelper);

    // Geometry Groups
    bim3d.elementsGroup = new THREE.Group();
    bim3d.scene.add(bim3d.elementsGroup);

    bim3d.survivabilityGroup = new THREE.Group();
    bim3d.scene.add(bim3d.survivabilityGroup);

    bim3d.isInitialized = true;

    // Load initial 3D BIM scene
    fetchBIMScene();
  }

  async function fetchBIMScene() {
    if (!bim3d.isInitialized || state.bimSceneLoading) return;
    try {
      state.bimSceneLoading = true;
      const res = await fetch(`${CONFIG.httpFallbackBase}/api/bim/scene`);
      if (res.ok) {
        const sceneData = await res.json();
        state.bimSceneData = sceneData;
        rebuildBIMMeshes(sceneData);
      }
    } catch (err) {
      console.warn('Failed to fetch BIM scene:', err);
    } finally {
      state.bimSceneLoading = false;
    }
  }

  function rebuildBIMMeshes(sceneData) {
    if (!bim3d.isInitialized || !sceneData || !sceneData.elements) return;

    // Dispose old meshes
    while (bim3d.elementsGroup.children.length > 0) {
      const obj = bim3d.elementsGroup.children[0];
      bim3d.elementsGroup.remove(obj);
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        if (Array.isArray(obj.material)) obj.material.forEach(m => m.dispose());
        else obj.material.dispose();
      }
    }

    let sumX = 0, sumZ = 0, countPts = 0;

    for (let i = 0; i < sceneData.elements.length; i++) {
      const el = sceneData.elements[i];
      if (!el.faces || el.faces.length === 0 || !el.vertices_3d) continue;

      const positions = [];
      for (let fIdx = 0; fIdx < el.faces.length; fIdx++) {
        const [i0, i1, i2] = el.faces[fIdx];
        if (i0 < el.vertices_3d.length && i1 < el.vertices_3d.length && i2 < el.vertices_3d.length) {
          const v0 = el.vertices_3d[i0];
          const v1 = el.vertices_3d[i1];
          const v2 = el.vertices_3d[i2];
          // Floor plan (x, y, z) -> Three.js (x, z_elevation, y_plan)
          positions.push(v0[0], v0[2], v0[1]);
          positions.push(v1[0], v1[2], v1[1]);
          positions.push(v2[0], v2[2], v2[1]);

          sumX += v0[0];
          sumZ += v0[1];
          countPts++;
        }
      }

      if (positions.length === 0) continue;

      const geom = new THREE.BufferGeometry();
      geom.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
      geom.computeVertexNormals();

      const col = new THREE.Color(el.color_hex || '#FFFFFF');
      let mat;

      if (el.element_type === 'window') {
        mat = new THREE.MeshStandardMaterial({
          color: col,
          transparent: true,
          opacity: el.opacity !== undefined ? el.opacity : 0.45,
          roughness: 0.1,
          metalness: 0.1,
          side: THREE.DoubleSide,
        });
      } else if (el.element_type === 'conduit') {
        mat = new THREE.MeshStandardMaterial({
          color: col,
          roughness: 0.25,
          metalness: 0.7,
          side: THREE.DoubleSide,
        });
      } else if (el.element_type === 'wall' || el.element_type === 'door_lintel') {
        mat = new THREE.MeshStandardMaterial({
          color: col,
          roughness: 0.85,
          metalness: 0.1,
          side: THREE.DoubleSide,
        });
      } else if (el.element_type === 'slab') {
        mat = new THREE.MeshStandardMaterial({
          color: col,
          roughness: 0.9,
          metalness: 0.05,
          side: THREE.DoubleSide,
        });
      } else {
        // device pod, equipment cabinet
        mat = new THREE.MeshStandardMaterial({
          color: col,
          roughness: 0.35,
          metalness: 0.45,
          side: THREE.DoubleSide,
        });
      }

      const mesh = new THREE.Mesh(geom, mat);
      mesh.name = el.element_id;
      mesh.castShadow = (el.element_type !== 'slab');
      mesh.receiveShadow = true;
      mesh.userData = el;
      bim3d.elementsGroup.add(mesh);
    }

    if (countPts > 0 && bim3d.controls) {
      const centerX = sumX / countPts;
      const centerZ = sumZ / countPts;
      bim3d.controls.target.set(centerX, 1.35, centerZ);
      if (bim3d.gridHelper) {
        bim3d.gridHelper.position.set(centerX, -0.01, centerZ);
      }
    }

    updateBIMSurvivability();
  }

  function updateBIMSurvivability() {
    if (!bim3d.isInitialized || !bim3d.survivabilityGroup) return;

    while (bim3d.survivabilityGroup.children.length > 0) {
      const child = bim3d.survivabilityGroup.children[0];
      bim3d.survivabilityGroup.remove(child);
      if (child.geometry) child.geometry.dispose();
      if (child.material) child.material.dispose();
    }

    if (!state.activeCutReport || !state.activeCutReport.cut_point) return;

    const cutPt = state.activeCutReport.cut_point;
    // Map cut point (x, y) to Three.js: (cutPt.x, 2.55, cutPt.y)
    const cutGeom = new THREE.SphereGeometry(0.12, 16, 16);
    const cutMat = new THREE.MeshBasicMaterial({ color: 0xff0055 });
    const cutMesh = new THREE.Mesh(cutGeom, cutMat);
    cutMesh.position.set(cutPt.x, 2.55, cutPt.y);
    bim3d.survivabilityGroup.add(cutMesh);

    // Glowing cut halo ring
    const ringGeom = new THREE.RingGeometry(0.15, 0.28, 32);
    ringGeom.rotateX(-Math.PI / 2);
    const ringMat = new THREE.MeshBasicMaterial({ color: 0xff1744, side: THREE.DoubleSide });
    const ringMesh = new THREE.Mesh(ringGeom, ringMat);
    ringMesh.position.set(cutPt.x, 2.56, cutPt.y);
    bim3d.survivabilityGroup.add(ringMesh);
  }

  function render3D() {
    if (!bim3d.isInitialized || !bim3d.renderer || state.viewMode === '2d') return;

    if (bim3d.controls) {
      bim3d.controls.update();
    }

    // Animate survivability pulse in 3D if wire cut is active
    if (state.activeCutReport && bim3d.survivabilityGroup.children.length >= 2) {
      const ring = bim3d.survivabilityGroup.children[1];
      const scale = 1.0 + 0.3 * Math.sin(state.pulsePhase * 2);
      ring.scale.set(scale, scale, scale);
    }

    bim3d.renderer.render(bim3d.scene, bim3d.camera);
  }

  function update3DDragMesh(target) {
    if (!bim3d.isInitialized || !bim3d.elementsGroup || !target) return;
    const meshName = target.type === 'node' ? `node_${target.id}` : `hub_${target.id}`;
    const mesh = bim3d.elementsGroup.getObjectByName(meshName);
    if (mesh) {
      // In Three.js: X is x, Y is z_elevation, Z is y_plan
      const dx = target.currentX - target.initialX;
      const dz = target.currentY - target.initialY;
      mesh.position.set(dx, 0, dz);
    }
  }

  function reset3DDragMesh(target) {
    if (!bim3d.isInitialized || !bim3d.elementsGroup || !target) return;
    const meshName = target.type === 'node' ? `node_${target.id}` : `hub_${target.id}`;
    const mesh = bim3d.elementsGroup.getObjectByName(meshName);
    if (mesh) {
      mesh.position.set(0, 0, 0);
    }
  }

  function setCameraPreset(mode) {
    if (!bim3d.isInitialized || !bim3d.controls || !bim3d.camera) return;

    const target = bim3d.controls.target.clone();
    ['btnCamIsometric', 'btnCamTopDown', 'btnCamPerspective'].forEach(id => {
      const btn = document.getElementById(id);
      if (btn) btn.classList.remove('active');
    });

    if (mode === 'isometric') {
      const btn = document.getElementById('btnCamIsometric');
      if (btn) btn.classList.add('active');
      bim3d.camera.position.set(target.x + 12, target.y + 14, target.z + 14);
      bim3d.camera.lookAt(target);
    } else if (mode === 'topdown') {
      const btn = document.getElementById('btnCamTopDown');
      if (btn) btn.classList.add('active');
      bim3d.camera.position.set(target.x, target.y + 26, target.z + 0.001);
      bim3d.camera.lookAt(target);
    } else if (mode === 'perspective') {
      const btn = document.getElementById('btnCamPerspective');
      if (btn) btn.classList.add('active');
      bim3d.camera.position.set(target.x - 5, target.y + 1.2, target.z - 5);
      bim3d.camera.lookAt(target);
    } else if (mode === 'reset') {
      setCameraPreset('isometric');
    }
    bim3d.controls.update();
  }

  function setViewMode(mode) {
    state.viewMode = mode;
    const vpContainer = document.getElementById('viewportContainer');
    if (!vpContainer) return;

    vpContainer.className = `viewport-container view-mode-${mode}`;

    ['btnView2D', 'btnViewSplit', 'btnView3D'].forEach(id => {
      const btn = document.getElementById(id);
      if (btn) btn.classList.remove('active');
    });

    if (mode === '2d') {
      const b = document.getElementById('btnView2D');
      if (b) b.classList.add('active');
    } else if (mode === 'split') {
      const b = document.getElementById('btnViewSplit');
      if (b) b.classList.add('active');
    } else if (mode === '3d') {
      const b = document.getElementById('btnView3D');
      if (b) b.classList.add('active');
    }

    setTimeout(() => {
      resizeCanvas();
      drawScene();
      if (bim3d.isInitialized && bim3d.renderer && bim3d.container) {
        const w = bim3d.container.clientWidth;
        const h = bim3d.container.clientHeight;
        bim3d.camera.aspect = w / (h || 1);
        bim3d.camera.updateProjectionMatrix();
        bim3d.renderer.setSize(w, h);
      }
    }, 50);
  }

  // --- Attach BIM and View Controls Listeners ---
  const btnView2D = document.getElementById('btnView2D');
  const btnViewSplit = document.getElementById('btnViewSplit');
  const btnView3D = document.getElementById('btnView3D');
  if (btnView2D) btnView2D.addEventListener('click', () => setViewMode('2d'));
  if (btnViewSplit) btnViewSplit.addEventListener('click', () => setViewMode('split'));
  if (btnView3D) btnView3D.addEventListener('click', () => setViewMode('3d'));

  const btnCamIsometric = document.getElementById('btnCamIsometric');
  const btnCamTopDown = document.getElementById('btnCamTopDown');
  const btnCamPerspective = document.getElementById('btnCamPerspective');
  const btnCamReset = document.getElementById('btnCamReset');
  if (btnCamIsometric) btnCamIsometric.addEventListener('click', () => setCameraPreset('isometric'));
  if (btnCamTopDown) btnCamTopDown.addEventListener('click', () => setCameraPreset('topdown'));
  if (btnCamPerspective) btnCamPerspective.addEventListener('click', () => setCameraPreset('perspective'));
  if (btnCamReset) btnCamReset.addEventListener('click', () => setCameraPreset('reset'));

  const btnDownloadBimObj = document.getElementById('btnDownloadBimObj');
  if (btnDownloadBimObj) {
    btnDownloadBimObj.addEventListener('click', () => {
      window.location.href = `${CONFIG.httpFallbackBase}/api/bim/export/obj`;
    });
  }

  // --- Window Resize & Initialization ---
  window.addEventListener('resize', () => {
    resizeCanvas();
    drawScene();
    if (bim3d.isInitialized && bim3d.renderer && bim3d.container) {
      const w = bim3d.container.clientWidth;
      const h = bim3d.container.clientHeight;
      if (w > 0 && h > 0) {
        bim3d.camera.aspect = w / h;
        bim3d.camera.updateProjectionMatrix();
        bim3d.renderer.setSize(w, h);
      }
    }
  });

  window.addEventListener('DOMContentLoaded', () => {
    resizeCanvas();
    initThreeEngine();
    connectWebSocket();
    requestAnimationFrame(renderLoop);
  });
})();
