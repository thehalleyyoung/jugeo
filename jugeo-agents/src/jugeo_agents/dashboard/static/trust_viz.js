/* JuGeo Trust Visualization — renders trust bars, agent cards, and obstruction list */

const TRUST_COLORS = {
    FORMALLY_PROVEN: '#065f46',
    HUMAN_VERIFIED: '#047857',
    TOOL_VERIFIED: '#059669',
    TOOL_EXECUTED: '#10b981',
    RAG_GROUNDED: '#6366f1',
    CITATION_BACKED: '#818cf8',
    CROSS_AGENT_CONFIRMED: '#a78bfa',
    STRONG_MODEL_GENERATED: '#f59e0b',
    WEAK_MODEL_GENERATED: '#f97316',
    UNGROUNDED_CLAIM: '#ef4444',
    SELF_CONTRADICTED: '#991b1b',
};

const TRUST_ORDER = [
    'FORMALLY_PROVEN', 'HUMAN_VERIFIED', 'TOOL_VERIFIED', 'TOOL_EXECUTED',
    'RAG_GROUNDED', 'CITATION_BACKED', 'CROSS_AGENT_CONFIRMED',
    'STRONG_MODEL_GENERATED', 'WEAK_MODEL_GENERATED',
    'UNGROUNDED_CLAIM', 'SELF_CONTRADICTED',
];

function renderTrustBars(data) {
    const container = document.getElementById('trust-bars');
    if (!container) return;
    container.innerHTML = '';
    const total = Object.values(data).reduce((a, b) => a + b, 0) || 1;

    for (const level of TRUST_ORDER) {
        const count = data[level] || 0;
        if (count === 0) continue;
        const pct = (count / total * 100).toFixed(0);
        const bar = document.createElement('div');
        bar.className = 'trust-bar';
        bar.innerHTML = `
            <span class="trust-bar-label">${level.replace(/_/g, ' ')}</span>
            <div class="trust-bar-fill" style="width:${Math.max(pct, 5)}%;background:${TRUST_COLORS[level]}">
                ${count} (${pct}%)
            </div>
        `;
        container.appendChild(bar);
    }
}

function renderAgents(agents) {
    const grid = document.getElementById('agents-grid');
    if (!grid) return;
    grid.innerHTML = '';

    for (const agent of agents) {
        const card = document.createElement('div');
        card.className = 'agent-card';
        let claimsHtml = '';
        for (const c of agent.claims) {
            claimsHtml += `
                <div class="claim-item" style="background:${c.color}22">
                    <span class="claim-icon">${c.icon}</span>
                    <span class="claim-text">${escapeHtml(c.text)}</span>
                    <span class="claim-trust" style="background:${c.color};color:#fff">
                        ${c.trust.replace(/_/g, ' ')}
                    </span>
                </div>
            `;
        }
        card.innerHTML = `
            <h3>
                <span>🤖 ${escapeHtml(agent.agent_id)}</span>
                <span style="color:${TRUST_COLORS[agent.trust] || '#888'}">${agent.trust}</span>
            </h3>
            <div style="color:#94a3b8;font-size:0.8rem;margin-bottom:0.5rem">
                Model: ${agent.model || 'unknown'} · ${agent.n_claims} claims
            </div>
            ${claimsHtml}
        `;
        grid.appendChild(card);
    }
}

function renderObstructions(obstructions) {
    const list = document.getElementById('obstructions-list');
    if (!list) return;
    list.innerHTML = '';

    if (obstructions.length === 0) {
        list.innerHTML = '<div style="color:#059669">✅ No obstructions detected</div>';
        return;
    }

    for (const o of obstructions) {
        const cls = o.cohomology === 'H2' ? 'h2' : o.cohomology === 'phantom' ? 'phantom' : '';
        const item = document.createElement('div');
        item.className = `obstruction-item ${cls}`;
        item.innerHTML = `
            <div class="obstruction-kind">${o.cohomology} · ${o.kind.replace(/_/g, ' ')}</div>
            <div class="obstruction-class">Agents: ${o.agents.join(', ')} · ${o.n_contradictions} contradiction(s)</div>
            <div style="font-size:0.8rem;margin-top:0.25rem">${escapeHtml(o.description)}</div>
        `;
        list.appendChild(item);
    }
}

function renderConvergence(data) {
    const canvas = document.getElementById('convergence-chart');
    if (!canvas || data.length === 0) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    const pad = 40;
    const plotW = W - 2 * pad, plotH = H - 2 * pad;

    // Axes
    ctx.strokeStyle = '#334155';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(pad, pad);
    ctx.lineTo(pad, H - pad);
    ctx.lineTo(W - pad, H - pad);
    ctx.stroke();

    // Labels
    ctx.fillStyle = '#94a3b8';
    ctx.font = '11px monospace';
    ctx.fillText('V(t)', pad + 4, pad - 8);
    ctx.fillText('Round', W - pad - 30, H - pad + 16);

    const maxV = Math.max(...data.map(d => d.lyapunov), 1);
    const n = data.length;

    function x(i) { return pad + (i / Math.max(n - 1, 1)) * plotW; }
    function y(v) { return H - pad - (v / maxV) * plotH; }

    // Grid lines
    ctx.strokeStyle = '#1e293b';
    for (let i = 0; i <= 4; i++) {
        const gy = pad + (i / 4) * plotH;
        ctx.beginPath(); ctx.moveTo(pad, gy); ctx.lineTo(W - pad, gy); ctx.stroke();
        ctx.fillStyle = '#64748b';
        ctx.fillText((maxV * (1 - i / 4)).toFixed(2), 2, gy + 4);
    }

    // Lines for each metric
    const series = [
        { key: 'lyapunov', color: '#ef4444', label: 'Lyapunov V' },
        { key: 'coverage', color: '#059669', label: 'Coverage' },
        { key: 'consistency', color: '#6366f1', label: 'Consistency' },
        { key: 'trust', color: '#f59e0b', label: 'Trust' },
    ];

    for (const s of series) {
        ctx.strokeStyle = s.color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
            const px = x(i), py = y(data[i][s.key] * maxV);
            if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
        ctx.stroke();
    }

    // Legend
    let lx = pad + 10;
    for (const s of series) {
        ctx.fillStyle = s.color;
        ctx.fillRect(lx, pad + 5, 12, 3);
        ctx.fillStyle = '#94a3b8';
        ctx.fillText(s.label, lx + 16, pad + 10);
        lx += ctx.measureText(s.label).width + 30;
    }
}

function updateSummary(data) {
    const el = document.getElementById('summary-text');
    if (!el) return;
    el.textContent =
        `Agents: ${data.total_agents}  Claims: ${data.total_claims}  Rounds: ${data.total_rounds}\n` +
        `Coverage: ${(data.coverage_score * 100).toFixed(0)}%` +
        (data.coverage_complete ? ' ✅' : ` — gaps: ${data.coverage_gaps.join(', ')}`) + '\n' +
        `Consistency: ${(data.consistency_score * 100).toFixed(0)}%` +
        (data.is_consistent ? ' ✅' : '') + '\n' +
        `Obstructions: ${data.n_obstructions}\n` +
        `Treaties: ${data.n_treaties}  Challenges: ${data.n_challenges}\n` +
        `Phase: ${data.phase}  Lyapunov V: ${data.lyapunov.toFixed(4)}`;

    document.getElementById('phase-badge').textContent = data.phase;
    document.getElementById('phase-badge').style.background =
        data.phase === 'complete' ? '#059669' :
        data.phase === 'verification' ? '#6366f1' :
        data.phase === 'resolution' ? '#f59e0b' : '#334155';
    document.getElementById('claims-count').textContent = `${data.total_claims} claims`;
    document.getElementById('obstruction-count').textContent = `${data.n_obstructions} obstructions`;
}

function escapeHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

async function refresh() {
    try {
        const [status, agents, obs, conv, trust] = await Promise.all([
            fetch('/api/status').then(r => r.json()),
            fetch('/api/agents').then(r => r.json()),
            fetch('/api/obstructions').then(r => r.json()),
            fetch('/api/convergence').then(r => r.json()),
            fetch('/api/trust-summary').then(r => r.json()),
        ]);
        updateSummary(status);
        renderTrustBars(trust);
        renderAgents(agents);
        renderObstructions(obs);
        renderConvergence(conv);
    } catch (e) {
        console.error('Refresh failed:', e);
    }
}

// Auto-refresh every 3 seconds
setInterval(refresh, 3000);
document.addEventListener('DOMContentLoaded', refresh);
