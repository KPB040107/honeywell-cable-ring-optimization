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

    if (data.type === 'state_update') {
      const firstLoad = !state.serverState;
      state.serverState = data;
      if (firstLoad) {
        fitToScreen();
      }
      updateHUD(data);
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

    // 4. Draw Cable Routes
    drawRoutes(state.serverState.routes || []);

    // 5. Draw Hubs
    drawHubs(state.serverState.hubs || []);

    // 6. Draw Fixed Nodes
    drawNodes(state.serverState.nodes || []);
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

      segments.forEach((seg, sIdx) => {
        const pts = seg.polyline || [];
        if (pts.length < 2) return;

        ctx.save();
        ctx.strokeStyle = ringColor;
        ctx.lineWidth = 3.5;
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

        // Directional flow arrow and sequence badge
        if (state.showBadges && pts.length >= 2) {
          const midIdx = Math.floor(pts.length / 2);
          const pA = worldToScreen(pts[midIdx - 1][0], pts[midIdx - 1][1]);
          const pB = worldToScreen(pts[midIdx][0], pts[midIdx][1]);
          drawFlowArrow(pA, pB, ringColor);
        }

        ctx.restore();
      });
    });
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

      // Circle badge
      ctx.beginPath();
      ctx.arc(scr.x, scr.y, 11, 0, Math.PI * 2);
      ctx.fillStyle = '#0F172A';
      ctx.fill();
      ctx.strokeStyle = '#38BDF8';
      ctx.lineWidth = 2.5;
      ctx.stroke();

      // Label
      ctx.fillStyle = '#FFFFFF';
      ctx.font = '700 9px JetBrains Mono, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(node.node_id, scr.x, scr.y);

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

    if (state.dragTarget) {
      canvas.style.cursor = 'grabbing';
      state.dragTarget.currentX = worldPos.x;
      state.dragTarget.currentY = worldPos.y;

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
      // Hover cursor
      const hit = findHitEntity(pos.x, pos.y);
      canvas.style.cursor = hit ? 'pointer' : 'crosshair';
    }
  });

  window.addEventListener('mouseup', function () {
    if (state.isPanning) {
      state.isPanning = false;
      canvas.style.cursor = 'crosshair';
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

  // --- Window Resize & Initialization ---
  window.addEventListener('resize', () => {
    resizeCanvas();
    drawScene();
  });

  window.addEventListener('DOMContentLoaded', () => {
    resizeCanvas();
    connectWebSocket();
    requestAnimationFrame(renderLoop);
  });
})();
