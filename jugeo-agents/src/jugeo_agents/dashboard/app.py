"""Flask dashboard for real-time multi-agent verification visualization.

Provides a web UI showing:
- Trust-colored claims per agent
- Obstruction timeline
- Provenance graph
- Convergence chart
- Pipeline summary

Requires::

    pip install jugeo-agents[dashboard]

Usage::

    from jugeo_agents.dashboard import create_app
    app = create_app(jugeo_wrapper)
    app.run(port=5050)
"""

from __future__ import annotations

import json
import os
from typing import Any

from jugeo_agents.types import (
    TrustLevel,
    CohomologyClass,
    ConvergencePhase,
    PipelineReport,
)

try:
    from flask import Flask, render_template, jsonify, request as flask_request
    _HAS_FLASK = True
except ImportError:
    _HAS_FLASK = False

try:
    from flask_socketio import SocketIO, emit
    _HAS_SOCKETIO = True
except ImportError:
    _HAS_SOCKETIO = False


# ---------------------------------------------------------------------------
# Trust level → CSS color mapping
# ---------------------------------------------------------------------------

TRUST_COLORS: dict[str, str] = {
    "FORMALLY_PROVEN": "#065f46",
    "HUMAN_VERIFIED": "#047857",
    "TOOL_VERIFIED": "#059669",
    "TOOL_EXECUTED": "#10b981",
    "RAG_GROUNDED": "#6366f1",
    "CITATION_BACKED": "#818cf8",
    "CROSS_AGENT_CONFIRMED": "#a78bfa",
    "STRONG_MODEL_GENERATED": "#f59e0b",
    "WEAK_MODEL_GENERATED": "#f97316",
    "UNGROUNDED_CLAIM": "#ef4444",
    "SELF_CONTRADICTED": "#991b1b",
}

TRUST_ICONS: dict[str, str] = {
    "FORMALLY_PROVEN": "✅",
    "HUMAN_VERIFIED": "✅",
    "TOOL_VERIFIED": "🔧✅",
    "TOOL_EXECUTED": "🔧",
    "RAG_GROUNDED": "📚",
    "CITATION_BACKED": "📎",
    "CROSS_AGENT_CONFIRMED": "🤝",
    "STRONG_MODEL_GENERATED": "🤖",
    "WEAK_MODEL_GENERATED": "⚠️",
    "UNGROUNDED_CLAIM": "❌",
    "SELF_CONTRADICTED": "🔴",
}


def create_app(jugeo_wrapper: Any = None) -> Any:
    """Create the Flask dashboard application.

    Parameters
    ----------
    jugeo_wrapper : JuGeoAgentWrapper, optional
        If provided, the dashboard reads live data from this wrapper.
        If None, the dashboard starts empty and accepts data via API.

    Returns
    -------
    Flask
        The configured Flask application.
    """
    if not _HAS_FLASK:
        raise ImportError(
            "Flask is required for the dashboard.  "
            "Install with: pip install jugeo-agents[dashboard]"
        )

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )
    app.config["SECRET_KEY"] = "jugeo-agents-dashboard"

    socketio = SocketIO(app) if _HAS_SOCKETIO else None

    # Mutable state for live updates
    _wrapper = {"ref": jugeo_wrapper}
    _events: list[dict[str, Any]] = []

    # ---- Routes ---------------------------------------------------------

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/api/status")
    def api_status():
        w = _wrapper["ref"]
        if w is None:
            return jsonify({"status": "no_wrapper", "agents": {}})

        report = w.on_pipeline_complete()
        return jsonify(_serialize_report(report))

    @app.route("/api/trust-summary")
    def api_trust_summary():
        w = _wrapper["ref"]
        if w is None:
            return jsonify({})
        return jsonify(w.trust_summary())

    @app.route("/api/agents")
    def api_agents():
        w = _wrapper["ref"]
        if w is None:
            return jsonify([])
        agents = []
        for agent_id, outputs in w._agent_outputs.items():
            claims = []
            for out in outputs:
                for c in out.claims:
                    claims.append({
                        "text": c.text,
                        "trust": c.trust.name,
                        "color": TRUST_COLORS.get(c.trust.name, "#888"),
                        "icon": TRUST_ICONS.get(c.trust.name, "?"),
                        "subject": c.subject,
                        "value": c.value,
                    })
            agents.append({
                "agent_id": agent_id,
                "model": outputs[-1].model if outputs else "",
                "trust": outputs[-1].trust.name if outputs else "UNKNOWN",
                "claims": claims,
                "n_claims": len(claims),
            })
        return jsonify(agents)

    @app.route("/api/obstructions")
    def api_obstructions():
        w = _wrapper["ref"]
        if w is None:
            return jsonify([])
        descent = w._descent.global_status()
        obs = []
        for o in descent.obstructions:
            obs.append({
                "kind": o.kind.name,
                "cohomology": o.cohomology.value,
                "agents": o.agents_involved,
                "description": o.description,
                "n_contradictions": len(o.contradictions),
            })
        return jsonify(obs)

    @app.route("/api/convergence")
    def api_convergence():
        w = _wrapper["ref"]
        if w is None:
            return jsonify([])
        history = w._convergence.history()
        return jsonify([
            {
                "round": s.round_number,
                "coverage": s.coverage,
                "consistency": s.consistency,
                "trust": s.trust_level,
                "lyapunov": s.lyapunov_v,
                "phase": s.phase.value,
            }
            for s in history
        ])

    @app.route("/api/provenance/<claim_text>")
    def api_provenance(claim_text: str):
        w = _wrapper["ref"]
        if w is None:
            return jsonify({"error": "no wrapper"})
        chain = w.provenance_for(claim_text)
        if chain is None:
            return jsonify({"error": "claim not found"})
        return jsonify({
            "claim": chain.claim.text,
            "overall_trust": chain.overall_trust.name,
            "links": [
                {
                    "agent": lk.agent_id,
                    "action": lk.action,
                    "trust": lk.trust.name,
                    "source": lk.source,
                }
                for lk in chain.links
            ],
        })

    @app.route("/api/events")
    def api_events():
        return jsonify(_events)

    @app.route("/api/push", methods=["POST"])
    def api_push():
        """Push an agent output into the wrapper via HTTP."""
        w = _wrapper["ref"]
        if w is None:
            return jsonify({"error": "no wrapper"}), 400
        data = flask_request.get_json(force=True)
        result = w.on_agent_output(
            agent_id=data.get("agent_id", "unknown"),
            output=data.get("output", ""),
            metadata=data.get("metadata", {}),
        )
        event = {
            "type": "agent_output",
            "agent_id": data.get("agent_id"),
            "status": result.status,
            "trust": result.trust_level.name,
            "n_obstructions": len(result.obstructions),
        }
        _events.append(event)
        if socketio is not None:
            socketio.emit("verification_update", event)
        return jsonify(event)

    # ---- Socket.IO events -----------------------------------------------

    if socketio is not None:

        @socketio.on("connect")
        def on_connect():
            emit("connected", {"status": "ok"})

        @socketio.on("request_status")
        def on_request_status():
            w = _wrapper["ref"]
            if w:
                report = w.on_pipeline_complete()
                emit("status_update", _serialize_report(report))

    # ---- Helpers --------------------------------------------------------

    def _serialize_report(report: PipelineReport) -> dict[str, Any]:
        return {
            "total_agents": report.total_agents,
            "total_claims": report.total_claims,
            "total_rounds": report.total_rounds,
            "coverage_score": report.coverage.coverage_score,
            "coverage_complete": report.coverage.is_complete,
            "coverage_gaps": list(report.coverage.gaps),
            "consistency_score": report.descent_result.consistency_score,
            "is_consistent": report.descent_result.is_consistent,
            "n_obstructions": len(report.descent_result.obstructions),
            "trust_summary": report.trust_summary,
            "phase": report.final_phase.value,
            "lyapunov": report.final_lyapunov,
            "n_treaties": len(report.treaties),
            "n_challenges": len(report.challenges),
        }

    app.socketio = socketio  # type: ignore[attr-defined]
    return app


def main() -> None:
    """Entry point for ``jugeo-dashboard`` CLI command."""
    from jugeo_agents.wrapper import JuGeoAgentWrapper

    jugeo = JuGeoAgentWrapper()
    app = create_app(jugeo)
    port = int(os.environ.get("JUGEO_DASHBOARD_PORT", "5050"))
    print(f"🔍 JuGeo Agent Dashboard running at http://localhost:{port}")
    if _HAS_SOCKETIO and app.socketio:
        app.socketio.run(app, host="0.0.0.0", port=port, debug=True)
    else:
        app.run(host="0.0.0.0", port=port, debug=True)
