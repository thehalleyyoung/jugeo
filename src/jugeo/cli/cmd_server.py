"""CLI subcommand handler for ``jugeo server``.

Exposes judgment-geometric operations as REST endpoints.  Each endpoint
creates real JuGeo objects (Sites, Judgments, DescentEngines, etc.) and
serializes them to JSON.  Falls back to AST-based lightweight handlers
when the full subsystem stack is unavailable.
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import os
import sys
import time
import traceback
import uuid
from functools import partial
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

_log = logging.getLogger(__name__)

_SERVER_START: float = 0.0

# -- geometry imports (all optional) -----------------------------------------
try:
    from jugeo.geometry.site import (
        Site,
        SiteBuilder,
        Coordinate,
        CoordinateKind,
        SiteSerializer,
        GrothendieckTopology,
    )
    _HAS_SITE = True
except Exception:
    _HAS_SITE = False

try:
    from jugeo.geometry.covers import Cover, score_cover
    _HAS_COVERS = True
except Exception:
    _HAS_COVERS = False

try:
    from jugeo.geometry.descent import (
        DescentEngine,
        DescentConfiguration,
        DescentStrategy,
    )
    _HAS_DESCENT = True
except Exception:
    _HAS_DESCENT = False

# -- judgment imports --------------------------------------------------------
try:
    from jugeo.judgments.judgment_terms import (
        Judgment,
        JudgmentBuilder,
        TrustLevel,
        Proposition,
        PropositionKind,
        Carrier,
        TrustAnnotation,
        JudgmentStatus,
        ProvenanceSource,
    )
    _HAS_JUDGMENTS = True
except Exception:
    _HAS_JUDGMENTS = False

# -- trust algebra -----------------------------------------------------------
try:
    from jugeo.evidence.trust import (
        TrustLevel as ETrustLevel,
        TrustAlgebra,
    )
    _HAS_TRUST = True
except Exception:
    _HAS_TRUST = False

# -- kernel lifecycle --------------------------------------------------------
try:
    from jugeo.kernel.lifecycle import (
        KernelPhase,
        LifecycleManager,
        BootSequence,
        HealthProbe,
    )
    _HAS_KERNEL = True
except Exception:
    _HAS_KERNEL = False

# -- encoding / prove / generate imports -------------------------------------
try:
    from jugeo.cli.cmd_encode import run_encode as _run_encode_cmd
    _HAS_ENCODE = True
except Exception:
    _HAS_ENCODE = False

try:
    from jugeo.cli.cmd_prove import run_prove as _run_prove_cmd
    _HAS_PROVE = True
except Exception:
    _HAS_PROVE = False

try:
    from jugeo.cli.cmd_generate import run_generate as _run_generate_cmd
    _HAS_GENERATE = True
except Exception:
    _HAS_GENERATE = False

try:
    from jugeo.packs.catalog import PackCatalog, PackDescriptor
    _HAS_PACKS = True
except Exception:
    _HAS_PACKS = False

# -- interfaces subsystem ---------------------------------------------------
try:
    from jugeo.interfaces.api import (  # type: ignore[import-untyped]
        JuGeoAPI,
    )
    _HAS_INTERFACES_API = True
except Exception:
    _HAS_INTERFACES_API = False

try:
    from jugeo.interfaces.task_router import (  # type: ignore[import-untyped]
        TaskRouter,
    )
    _HAS_TASK_ROUTER = True
except Exception:
    _HAS_TASK_ROUTER = False

try:
    from jugeo.interfaces.diagnostics import (  # type: ignore[import-untyped]
        DiagnosticsEngine,
        DiagnosticReport,
    )
    _HAS_DIAGNOSTICS = True
except Exception:
    _HAS_DIAGNOSTICS = False


# ======================================================================
# Kernel boot helper
# ======================================================================

def _boot_kernel(verbose: bool) -> dict[str, Any]:
    """Bootstrap the JuGeo kernel through lifecycle phases."""
    ctx: dict[str, Any] = {
        "phase": "uninitialized",
        "manager": None,
        "errors": [],
    }

    if not _HAS_KERNEL:
        ctx["errors"].append("kernel.lifecycle unavailable")
        return ctx

    try:
        mgr = LifecycleManager()
        boot = BootSequence(mgr)
        cert = boot.execute()
        ctx["manager"] = mgr
        ctx["phase"] = mgr.current_phase.value
        ctx["boot_id"] = boot.boot_id
        if verbose:
            _log.debug("Kernel boot complete: phase=%s", mgr.current_phase.value)
    except Exception as exc:
        ctx["errors"].append(f"boot failed: {exc}")
        _log.debug("Kernel boot failed: %s", exc)

    return ctx


# ======================================================================
# AST-based fallback helpers
# ======================================================================

def _ast_functions(source: str) -> list[dict[str, Any]]:
    """Extract function info from Python source via AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [{"error": str(exc)}]

    funcs: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append({
                "name": node.name,
                "lineno": node.lineno,
                "args": [a.arg for a in node.args.args],
                "decorators": [ast.dump(d) for d in node.decorator_list],
            })
    return funcs


def _read_source(path: str) -> str | None:
    """Read file contents, return None on failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except Exception:
        return None


# ======================================================================
# Site builder from source file
# ======================================================================

def _build_site_from_source(source: str, filepath: str) -> dict[str, Any]:
    """Build a Site from a Python source file, one coordinate per function."""
    if not _HAS_SITE:
        funcs = _ast_functions(source)
        return {"coordinates": funcs, "geometry_available": False}

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"error": str(exc)}

    builder = SiteBuilder(os.path.basename(filepath))
    coords: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            coord = Coordinate(
                node.name,
                kind=CoordinateKind.FUNCTION,
                metadata={"lineno": node.lineno, "file": filepath},
            )
            builder.add_coordinate(coord)
            coords.append({"name": node.name, "kind": "function",
                           "lineno": node.lineno})
        elif isinstance(node, ast.ClassDef):
            coord = Coordinate(
                node.name,
                kind=CoordinateKind.INTERFACE,
                metadata={"lineno": node.lineno, "file": filepath},
            )
            builder.add_coordinate(coord)
            coords.append({"name": node.name, "kind": "interface",
                           "lineno": node.lineno})

    try:
        topo = GrothendieckTopology.canonical()
        builder.set_topology(topo)
    except Exception:
        pass

    site = builder.build()
    result = SiteSerializer.site_to_json(site)
    result["coordinates_summary"] = coords
    return result


# ======================================================================
# Judgment builder for analysis
# ======================================================================

def _analyze_source(source: str, filepath: str) -> dict[str, Any]:
    """Build site + judgments from source code for the /analyze endpoint."""
    site_data = _build_site_from_source(source, filepath)
    result: dict[str, Any] = {"file": filepath, "site": site_data, "judgments": []}

    if not (_HAS_JUDGMENTS and _HAS_SITE):
        result["note"] = "judgment subsystem unavailable; returning site only"
        return result

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return result

    judgments: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            coord = Coordinate(node.name, kind=CoordinateKind.FUNCTION)
            has_return = any(
                isinstance(child, ast.Return) and child.value is not None
                for child in ast.walk(node)
            )
            has_docstring = (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            )
            formula = f"function({node.name}).well_formed"
            trust_lvl = TrustLevel.RUNTIME_WITNESSED if has_return else TrustLevel.COPILOT_SUGGESTED

            try:
                judgment = (
                    JudgmentBuilder()
                    .at(coord)
                    .claiming(Proposition(
                        kind=PropositionKind.STRUCTURAL,
                        formula=formula,
                    ))
                    .of_type_named("PythonFunction")
                    .with_trust_level(trust_lvl)
                    .from_source(ProvenanceSource.RUNTIME)
                    .build()
                )
                judgments.append(judgment.serialize())
            except Exception as exc:
                judgments.append({"function": node.name, "error": str(exc)})

    result["judgments"] = judgments
    return result


# ======================================================================
# HTTP request handler
# ======================================================================

class _JuGeoRequestHandler(BaseHTTPRequestHandler):
    """Judgment-geometric HTTP API handler."""

    _kernel_ctx: dict[str, Any] = {}
    _verbose: bool = False

    def log_message(self, fmt: str, *a: Any) -> None:
        if self._verbose:
            _log.debug(fmt, *a)

    # -- helpers -----------------------------------------------------------

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, Any]:
        raw = self._read_body()
        return json.loads(raw) if raw else {}

    def _respond_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_error(self, status: int, message: str) -> None:
        self._respond_json({"error": message}, status)

    # -- GET endpoints -----------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]
        params = self._parse_query()

        if path == "/health":
            self._handle_health()
        elif path == "/info":
            self._handle_info()
        elif path == "/site":
            self._handle_get_site(params)
        elif path == "/trust":
            self._handle_trust()
        elif path == "/descent":
            self._handle_get_descent(params)
        else:
            self._respond_error(404, f"Unknown endpoint: {path}")

    # -- POST endpoints ----------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?")[0]

        if path == "/analyze":
            self._handle_analyze()
        elif path == "/prove":
            self._handle_prove()
        elif path == "/encode":
            self._handle_encode()
        elif path == "/generate":
            self._handle_generate()
        else:
            self._respond_error(404, f"Unknown endpoint: {path}")

    # -- query parsing -----------------------------------------------------

    def _parse_query(self) -> dict[str, str]:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        return {k: v[0] for k, v in qs.items()}

    # -- GET /health -------------------------------------------------------

    def _handle_health(self) -> None:
        uptime = time.time() - _SERVER_START if _SERVER_START else 0
        data: dict[str, Any] = {
            "status": "ok",
            "uptime_s": round(uptime, 2),
            "geometry_available": _HAS_SITE,
            "judgments_available": _HAS_JUDGMENTS,
            "trust_available": _HAS_TRUST,
            "descent_available": _HAS_DESCENT,
            "kernel_phase": self._kernel_ctx.get("phase", "uninitialized"),
        }
        if _HAS_KERNEL:
            mgr = self._kernel_ctx.get("manager")
            if mgr:
                data["kernel_operational"] = mgr.is_operational
        self._respond_json(data)

    # -- GET /info ---------------------------------------------------------

    def _handle_info(self) -> None:
        from jugeo.cli.cmd_info import run_info as _info_fn
        import io, contextlib, argparse as ap

        args = ap.Namespace(
            packs=True, maturity=True, thesis=True, kernel=True,
            show_all=True, format="json", verbose=False,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                _info_fn(args)
            except Exception as exc:
                self._respond_error(500, str(exc))
                return
        try:
            data = json.loads(buf.getvalue())
        except Exception:
            data = {"raw": buf.getvalue()}
        self._respond_json(data)

    # -- GET /site?file=X --------------------------------------------------

    def _handle_get_site(self, params: dict[str, str]) -> None:
        filepath = params.get("file")
        if not filepath:
            self._respond_error(400, "Missing ?file= parameter")
            return
        source = _read_source(filepath)
        if source is None:
            self._respond_error(404, f"Cannot read: {filepath}")
            return
        site_data = _build_site_from_source(source, filepath)
        self._respond_json(site_data)

    # -- GET /trust --------------------------------------------------------

    def _handle_trust(self) -> None:
        if not _HAS_TRUST:
            self._respond_json({"available": False,
                                "note": "evidence.trust unavailable"})
            return

        algebra = TrustAlgebra()
        levels = []
        for lvl in ETrustLevel:
            levels.append({
                "name": lvl.name,
                "value": lvl.value,
                "rank": lvl.rank_index(),
            })
        data: dict[str, Any] = {
            "available": True,
            "levels": levels,
            "bottom": algebra.bottom().value,
            "top": algebra.top().value,
            "operations": {
                "meet(solver, runtime)": algebra.meet(
                    ETrustLevel.SOLVER_DISCHARGED,
                    ETrustLevel.RUNTIME_WITNESSED,
                ).value,
                "join(copilot, runtime)": algebra.join(
                    ETrustLevel.COPILOT_SUGGESTED,
                    ETrustLevel.RUNTIME_WITNESSED,
                ).value,
                "compose(solver, oracle)": algebra.compose(
                    ETrustLevel.SOLVER_DISCHARGED,
                    ETrustLevel.ORACLE_PROPOSED,
                ).value,
            },
        }
        self._respond_json(data)

    # -- GET /descent?file=X -----------------------------------------------

    def _handle_get_descent(self, params: dict[str, str]) -> None:
        filepath = params.get("file")
        if not filepath:
            self._respond_error(400, "Missing ?file= parameter")
            return
        if not (_HAS_DESCENT and _HAS_SITE and _HAS_COVERS):
            self._respond_error(501, "Descent subsystem unavailable")
            return

        source = _read_source(filepath)
        if source is None:
            self._respond_error(404, f"Cannot read: {filepath}")
            return

        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            self._respond_error(400, f"Parse error: {exc}")
            return

        # Build site and cover
        builder = SiteBuilder(os.path.basename(filepath))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                builder.add_coordinate(
                    Coordinate(node.name, kind=CoordinateKind.FUNCTION)
                )

        site = builder.build()
        config = DescentConfiguration(
            strategy=DescentStrategy.EXHAUSTIVE,
            depth_limit=3,
        )
        engine = DescentEngine(configuration=config)

        result: dict[str, Any] = {
            "file": filepath,
            "site_coordinates": site.coordinate_count(),
            "strategy": config.strategy.value,
            "depth_limit": config.depth_limit,
        }

        # Try descent if we have covers
        try:
            cover = Cover(
                target=Coordinate(
                    os.path.basename(filepath),
                    kind=CoordinateKind.MODULE,
                ),
            )
            metric = score_cover(cover)
            result["cover_score"] = metric.total_score
        except Exception as exc:
            result["cover_note"] = str(exc)

        self._respond_json(result)

    # -- POST /analyze -----------------------------------------------------

    def _handle_analyze(self) -> None:
        body = self._json_body()
        filepath = body.get("file")
        source = body.get("source")

        if not filepath and not source:
            self._respond_error(400, "Provide 'file' path or 'source' code")
            return

        if source is None:
            source = _read_source(filepath)
        if source is None:
            self._respond_error(404, f"Cannot read: {filepath}")
            return

        result = _analyze_source(source, filepath or "<input>")
        self._respond_json(result)

    # -- POST /prove -------------------------------------------------------

    def _handle_prove(self) -> None:
        if not _HAS_PROVE:
            self._respond_error(501, "Prove subsystem unavailable")
            return

        body = self._json_body()
        filepath = body.get("file")
        if not filepath:
            self._respond_error(400, "Provide 'file' path")
            return

        import io, contextlib, argparse as ap
        args = ap.Namespace(
            files=[filepath],
            trust_floor=body.get("trust_floor", "copilot_suggested"),
            max_depth=body.get("max_depth", 5),
            strategy=body.get("strategy", "exhaustive"),
            verbose=False,
            format="json",
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = _run_prove_cmd(args)
            except Exception as exc:
                self._respond_error(500, str(exc))
                return
        try:
            data = json.loads(buf.getvalue())
        except Exception:
            data = {"raw": buf.getvalue()}
        data["exit_code"] = rc
        self._respond_json(data)

    # -- POST /encode ------------------------------------------------------

    def _handle_encode(self) -> None:
        body = self._json_body()
        filepath = body.get("file")
        source = body.get("source")

        if not filepath and not source:
            self._respond_error(400, "Provide 'file' or 'source'")
            return

        if not _HAS_ENCODE:
            self._respond_error(501, "Encode subsystem unavailable")
            return

        import io, contextlib, argparse as ap
        args = ap.Namespace(
            files=[filepath] if filepath else [],
            format="json",
            verbose=False,
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = _run_encode_cmd(args)
            except Exception as exc:
                self._respond_error(500, str(exc))
                return
        try:
            data = json.loads(buf.getvalue())
        except Exception:
            data = {"raw": buf.getvalue()}
        data["exit_code"] = rc
        self._respond_json(data)

    # -- POST /generate ----------------------------------------------------

    def _handle_generate(self) -> None:
        if not _HAS_GENERATE:
            self._respond_error(501, "Generate subsystem unavailable")
            return

        body = self._json_body()
        goal = body.get("goal")
        if not goal:
            self._respond_error(400, "Provide 'goal' string")
            return

        import io, contextlib, argparse as ap
        args = ap.Namespace(goal=goal, format="json", verbose=False)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                rc = _run_generate_cmd(args)
            except Exception as exc:
                self._respond_error(500, str(exc))
                return
        try:
            data = json.loads(buf.getvalue())
        except Exception:
            data = {"raw": buf.getvalue()}
        data["exit_code"] = rc
        self._respond_json(data)


# ======================================================================
# Handler factory
# ======================================================================

def _make_handler_class(
    kernel_ctx: dict[str, Any],
    verbose: bool,
) -> type[_JuGeoRequestHandler]:
    """Create a handler subclass with injected kernel context."""

    class _Handler(_JuGeoRequestHandler):
        _kernel_ctx = kernel_ctx
        _verbose = verbose

    return _Handler


# ======================================================================
# Registry
# ======================================================================


def _server_registry() -> dict[str, type]:
    """Return a dict of all public classes from interfaces subpackages."""
    registry: dict[str, type] = {}

    try:
        from jugeo.interfaces.diagnostics import (  # type: ignore[import-untyped]
            DiagnosticLevel, DiagnosticMessage, DiagnosticReport,
            DiagnosticsEngine, VerifiedItem, VerificationStatusView,
            ResidualEntry, ResidualView, ObstructionEntry, ObstructionView,
            TrustDistributionSnapshot, TrustDistributionView, ChannelStats,
            EvidenceChannelView, FilterCriteria, DiagnosticFilter,
            DiagnosticExporter, DiagnosticHistory, DiagnosticSerializer,
        )
        registry["DiagnosticLevel"] = DiagnosticLevel
        registry["DiagnosticMessage"] = DiagnosticMessage
        registry["DiagnosticReport"] = DiagnosticReport
        registry["DiagnosticsEngine"] = DiagnosticsEngine
        registry["VerifiedItem"] = VerifiedItem
        registry["VerificationStatusView"] = VerificationStatusView
        registry["ResidualEntry"] = ResidualEntry
        registry["ResidualView"] = ResidualView
        registry["ObstructionEntry"] = ObstructionEntry
        registry["ObstructionView"] = ObstructionView
        registry["TrustDistributionSnapshot"] = TrustDistributionSnapshot
        registry["TrustDistributionView"] = TrustDistributionView
        registry["ChannelStats"] = ChannelStats
        registry["EvidenceChannelView"] = EvidenceChannelView
        registry["FilterCriteria"] = FilterCriteria
        registry["DiagnosticFilter"] = DiagnosticFilter
        registry["DiagnosticExporter"] = DiagnosticExporter
        registry["DiagnosticHistory"] = DiagnosticHistory
        registry["DiagnosticSerializer"] = DiagnosticSerializer
    except Exception:
        pass

    try:
        from jugeo.interfaces.api import (  # type: ignore[import-untyped]
            OperationKind, RequestStatus, APIRequest, APIResponse,
            APISession, APIAuthenticator, APIRateLimiter, APIValidator,
            APIRouter, APIEventLog, APISerializer, CopilotAPIBridge,
            JuGeoAPI,
        )
        registry["OperationKind"] = OperationKind
        registry["RequestStatus"] = RequestStatus
        registry["APIRequest"] = APIRequest
        registry["APIResponse"] = APIResponse
        registry["APISession"] = APISession
        registry["APIAuthenticator"] = APIAuthenticator
        registry["APIRateLimiter"] = APIRateLimiter
        registry["APIValidator"] = APIValidator
        registry["APIRouter"] = APIRouter
        registry["APIEventLog"] = APIEventLog
        registry["APISerializer"] = APISerializer
        registry["CopilotAPIBridge"] = CopilotAPIBridge
        registry["JuGeoAPI"] = JuGeoAPI
    except Exception:
        pass

    try:
        from jugeo.interfaces.cli import (  # type: ignore[import-untyped]
            ParserExit, HonestArgumentParser, OutputFormat, TrustLabel,
            ResidualKind, UsageKind, EvidenceRoute, ScopeCoordinate,
            ResidualObligation, PublicClaim, RouteBudget, FrontierNode,
            ControlSurface, SurfaceSnapshot, CLIContext, CLIApplication,
        )
        registry["ParserExit"] = ParserExit
        registry["HonestArgumentParser"] = HonestArgumentParser
        registry["OutputFormat"] = OutputFormat
        registry["TrustLabel"] = TrustLabel
        registry["ResidualKind"] = ResidualKind
        registry["UsageKind"] = UsageKind
        registry["EvidenceRoute"] = EvidenceRoute
        registry["ScopeCoordinate"] = ScopeCoordinate
        registry["ResidualObligation"] = ResidualObligation
        registry["PublicClaim"] = PublicClaim
        registry["RouteBudget"] = RouteBudget
        registry["FrontierNode"] = FrontierNode
        registry["ControlSurface"] = ControlSurface
        registry["SurfaceSnapshot"] = SurfaceSnapshot
        registry["CLIContext"] = CLIContext
        registry["CLIApplication"] = CLIApplication
    except Exception:
        pass

    return registry


# ======================================================================
# Entry point
# ======================================================================

def run_server(args: argparse.Namespace) -> int:
    """Start the JuGeo HTTP server.

    Parameters
    ----------
    args : argparse.Namespace
        Expected attributes:
        - ``host``    -- bind address (default ``"127.0.0.1"``)
        - ``port``    -- listen port (default ``8421``)
        - ``format``  -- output format for startup messages (``"text"``/``"json"``)
        - ``verbose`` -- enable debug logging

    Returns
    -------
    int
        0 on clean shutdown, 1 on error.
    """
    global _SERVER_START

    host: str = getattr(args, "host", "127.0.0.1")
    port: int = int(getattr(args, "port", 8421))
    out_format: str = getattr(args, "format", "text")
    verbose: bool = getattr(args, "verbose", False)

    if getattr(args, "registry", False):
        reg = _server_registry()
        for name, cls in sorted(reg.items()):
            print(f"  {name:40s} {cls.__module__}.{cls.__qualname__}")
        print(f"\n  Total: {len(reg)} classes")
        return 0

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s: %(message)s")

    # -- Boot kernel -------------------------------------------------------
    if verbose or out_format == "text":
        print("Booting JuGeo kernel \u2026")

    kernel_ctx = _boot_kernel(verbose)

    _SERVER_START = time.time()

    # -- Start server ------------------------------------------------------
    handler_cls = _make_handler_class(kernel_ctx, verbose)

    try:
        httpd = HTTPServer((host, port), handler_cls)
    except OSError as exc:
        print(f"error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 1

    startup_info: dict[str, Any] = {
        "host": host,
        "port": port,
        "kernel_phase": kernel_ctx.get("phase", "uninitialized"),
        "geometry_available": _HAS_SITE,
        "judgments_available": _HAS_JUDGMENTS,
        "trust_available": _HAS_TRUST,
        "descent_available": _HAS_DESCENT,
        "boot_errors": kernel_ctx.get("errors", []),
        "endpoints": {
            "POST": ["/analyze", "/prove", "/encode", "/generate"],
            "GET": ["/health", "/info", "/site", "/trust", "/descent"],
        },
    }

    if out_format == "json":
        print(json.dumps(startup_info, indent=2))
    else:
        print(f"JuGeo server listening on http://{host}:{port}")
        print(f"  kernel phase : {startup_info['kernel_phase']}")
        print(f"  geometry     : {'yes' if _HAS_SITE else 'fallback'}")
        print(f"  judgments    : {'yes' if _HAS_JUDGMENTS else 'fallback'}")
        print(f"  trust        : {'yes' if _HAS_TRUST else 'no'}")
        print(f"  descent      : {'yes' if _HAS_DESCENT else 'no'}")
        if startup_info["boot_errors"]:
            print(f"  boot warnings: {len(startup_info['boot_errors'])}")
            for err in startup_info["boot_errors"]:
                print(f"    \u2022 {err}")
        print("  POST endpoints: /analyze /prove /encode /generate")
        print("  GET  endpoints: /health /info /site /trust /descent")
        print("Press Ctrl+C to stop.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if out_format == "text":
            print("\nServer stopped.")

    return 0
