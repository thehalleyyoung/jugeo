"""jugeo.webapp — Web application synthesis module.

Orchestrates the full pipeline from ideation through specification,
generation, verification, and output for Flask web applications.

Usage:
    from jugeo.webapp import WebApplicationSite, FlaskAppGenerator
    from jugeo.webapp import IdeationPipeline, WebVerificationPipeline

Version: 0.1.0
"""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = [
    "WebApplicationSite",
    "FlaskAppGenerator",
    "IdeationPipeline",
    "WebVerificationPipeline",
    "WebDescentEngine",
]


def __getattr__(name: str):
    if name == "WebApplicationSite":
        from jugeo.webapp.site import WebApplicationSite
        return WebApplicationSite
    if name == "FlaskAppGenerator":
        try:
            from jugeo.webapp.generation import FlaskAppGenerator
            return FlaskAppGenerator
        except ImportError:
            return None
    if name == "IdeationPipeline":
        try:
            from jugeo.webapp.ideation import IdeationPipeline
            return IdeationPipeline
        except ImportError:
            return None
    if name == "WebVerificationPipeline":
        try:
            from jugeo.webapp.verification import WebVerificationPipeline
            return WebVerificationPipeline
        except ImportError:
            return None
    if name == "WebDescentEngine":
        try:
            from jugeo.webapp.descent import WebDescentEngine
            return WebDescentEngine
        except ImportError:
            return None
    raise AttributeError(f"module 'jugeo.webapp' has no attribute {name!r}")
