"""Flask app generation module for JuGeo webapp.

Exports all major generators, models, and utilities for producing
complete Flask projects from declarative specifications.
"""
from __future__ import annotations

from .models import (
    AppSpec,
    BlueprintSpec,
    ColumnSpec,
    ColumnType,
    ConfigSpec,
    FormFieldSpec,
    FormFieldType,
    FormSpec,
    GenerationResult,
    ModelSpec,
    ResponseType,
    RouteSpec,
    StaticFileSpec,
    TemplateSpec,
    TestCaseSpec,
    TestSpec,
)
from .obligations import (
    Obligation,
    ObligationKind,
    ObligationResult,
    ObligationReport,
    GenerationTarget,
    resolve_obligations,
    enforce_obligations,
    OBLIGATION_PRESETS,
)
from .flask_generator import FlaskAppGenerator
from .flask_obligations import (
    FlaskObligationChecker,
    FlaskSpecEnricher,
    FlaskObligation,
    FlaskObligationKind,
    FlaskObligationResult,
    FlaskObligationReport,
    FLASK_OBLIGATION_PRESETS,
)
from .html_generator import (
    HTMLOnlyGenerator,
    HTMLAppSpec,
    HTMLGenerationResult,
    PageSpec,
    PageKind,
    ComponentSpec,
    ComponentKind,
    HTMLObligationChecker,
    HTMLSpecEnricher,
    # backward compat aliases exported from html_generator
    VisualObligation,
    ObligationChecker,
    SpecEnricher,
)
from .copilot_driver import CopilotGenerationDriver, FiberKind, SectionProposal, copilot_generate
from .prompt_driver import PromptToApp, PromptToAppResult
from .concept_extractor import extract_concepts, ConceptMap, ConceptDomain, Concept
from .route_generator import RouteCodeGenerator, URLPatternGenerator
from .model_generator import ModelCodeGenerator, SchemaGenerator
from .template_generator import TemplateCodeGenerator, ComponentLibrary
from .static_generator import CSSGenerator, JSGenerator
from .test_generator import TestCodeGenerator
from .blueprint_architect import BlueprintArchitect, ArchitectureValidator
from .scaffold import AppScaffolder, SpecValidator
from .config_generator import ConfigCodeGenerator, DockerfileGenerator
from .migration_generator import MigrationCodeGenerator
from .app_runner import AppRunner, SyntaxChecker
from .verification_bridge import VerificationBridge

__all__ = [
    # Generators
    "FlaskAppGenerator",
    "HTMLOnlyGenerator",
    "RouteCodeGenerator",
    "URLPatternGenerator",
    "ModelCodeGenerator",
    "SchemaGenerator",
    "TemplateCodeGenerator",
    "ComponentLibrary",
    "CSSGenerator",
    "JSGenerator",
    "TestCodeGenerator",
    "BlueprintArchitect",
    "ArchitectureValidator",
    "AppScaffolder",
    "SpecValidator",
    "ConfigCodeGenerator",
    "DockerfileGenerator",
    "MigrationCodeGenerator",
    "AppRunner",
    "SyntaxChecker",
    "VerificationBridge",
    # Models
    "AppSpec",
    "RouteSpec",
    "ModelSpec",
    "ColumnSpec",
    "ColumnType",
    "ResponseType",
    "TemplateSpec",
    "StaticFileSpec",
    "BlueprintSpec",
    "ConfigSpec",
    "FormFieldSpec",
    "FormFieldType",
    "FormSpec",
    "TestCaseSpec",
    "TestSpec",
    "GenerationResult",
    # HTML-only models
    "HTMLAppSpec",
    "HTMLGenerationResult",
    "PageSpec",
    "PageKind",
    "ComponentSpec",
    "ComponentKind",
    # Shared obligation presheaf (from obligations.py)
    "Obligation",
    "ObligationKind",
    "ObligationResult",
    "ObligationReport",
    "GenerationTarget",
    "resolve_obligations",
    "enforce_obligations",
    "OBLIGATION_PRESETS",
    # HTML obligation implementations
    "HTMLObligationChecker",
    "HTMLSpecEnricher",
    # Flask obligation implementations
    "FlaskObligationChecker",
    "FlaskSpecEnricher",
    # Copilot driver
    "CopilotGenerationDriver",
    "FiberKind",
    "SectionProposal",
    "copilot_generate",
    # Prompt-to-app pipeline
    "PromptToApp",
    "PromptToAppResult",
    "extract_concepts",
    "ConceptMap",
    "ConceptDomain",
    "Concept",
    # Backward compat aliases
    "VisualObligation",
    "ObligationChecker",
    "SpecEnricher",
    "FlaskObligation",
    "FlaskObligationKind",
    "FlaskObligationResult",
    "FlaskObligationReport",
    "FLASK_OBLIGATION_PRESETS",
]
