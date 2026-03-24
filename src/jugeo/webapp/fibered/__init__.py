"""Fibered category model for web applications.

Implements the fibered category model from §7 of the Geometry of Web
Applications theory document. The web application is a fibered category:
total category = web site, base category = {Python, JS, HTML, CSS, SQL},
fibers = per-language sites.
"""
from __future__ import annotations

from .models import (
    LanguageFiber,
    FiberedCoordinate,
    CartesianLift,
    FiberDescentResult,
    FiberedSiteResult,
)
from .fibered_category import WebFiberedCategory
from .fiber_descent import FiberDescentEngine
from .language_fibers import (
    PythonFiber,
    JavaScriptFiber,
    CSSFiber,
    HTMLFiber,
    SQLFiber,
    TemplateFiber,
)

__all__ = [
    "LanguageFiber",
    "FiberedCoordinate",
    "CartesianLift",
    "FiberDescentResult",
    "FiberedSiteResult",
    "WebFiberedCategory",
    "FiberDescentEngine",
    "PythonFiber",
    "JavaScriptFiber",
    "CSSFiber",
    "HTMLFiber",
    "SQLFiber",
    "TemplateFiber",
]
