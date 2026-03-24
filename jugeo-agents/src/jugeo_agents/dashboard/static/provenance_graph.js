/* Provenance Graph Visualization — draws provenance chains as directed graphs */

class ProvenanceViz {
    constructor(canvasId) {
        this.canvas = document.getElementById(canvasId);
        this.ctx = this.canvas ? this.canvas.getContext('2d') : null;
    }

    async loadAndRender(claimText) {
        if (!this.ctx) return;
        try {
            const resp = await fetch(`/api/provenance/${encodeURIComponent(claimText)}`);
            const data = await resp.json();
            if (data.error) { console.warn(data.error); return; }
            this.render(data);
        } catch (e) { console.error('Provenance load failed:', e); }
    }

    render(data) {
        const ctx = this.ctx;
        const W = this.canvas.width, H = this.canvas.height;
        ctx.clearRect(0, 0, W, H);

        const links = data.links || [];
        if (links.length === 0) return;

        const nodeW = 140, nodeH = 50, gap = 40;
        const startX = 40;
        const centerY = H / 2;

        // Draw nodes (right to left = origin to final)
        const reversed = [...links].reverse();
        for (let i = 0; i < reversed.length; i++) {
            const lk = reversed[i];
            const x = startX + i * (nodeW + gap);
            const y = centerY - nodeH / 2;

            // Node box
            const color = TRUST_COLORS[lk.trust] || '#334155';
            ctx.fillStyle = color + '33';
            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.roundRect(x, y, nodeW, nodeH, 6);
            ctx.fill();
            ctx.stroke();

            // Agent name
            ctx.fillStyle = '#e2e8f0';
            ctx.font = 'bold 11px monospace';
            ctx.fillText(lk.agent, x + 8, y + 18);

            // Action
            ctx.fillStyle = '#94a3b8';
            ctx.font = '10px monospace';
            ctx.fillText(lk.action, x + 8, y + 32);

            // Trust badge
            ctx.fillStyle = color;
            ctx.font = 'bold 9px monospace';
            ctx.fillText(lk.trust, x + 8, y + 44);

            // Arrow to next node
            if (i < reversed.length - 1) {
                const ax = x + nodeW;
                const ay = centerY;
                const bx = ax + gap;
                ctx.strokeStyle = '#475569';
                ctx.lineWidth = 2;
                ctx.beginPath();
                ctx.moveTo(ax, ay);
                ctx.lineTo(bx, ay);
                ctx.stroke();
                // Arrowhead
                ctx.beginPath();
                ctx.moveTo(bx, ay);
                ctx.lineTo(bx - 6, ay - 4);
                ctx.lineTo(bx - 6, ay + 4);
                ctx.closePath();
                ctx.fillStyle = '#475569';
                ctx.fill();
            }
        }

        // Overall trust label
        ctx.fillStyle = TRUST_COLORS[data.overall_trust] || '#888';
        ctx.font = 'bold 12px monospace';
        ctx.fillText(`Overall: ${data.overall_trust}`, startX, centerY + nodeH / 2 + 24);
    }
}
