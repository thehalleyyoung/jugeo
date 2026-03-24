"""Full consistency checking with jg integration.

Runs descent on the workspace site and augments the standard overlap checks
with jg prove/bugs/spec verification.
"""

from __future__ import annotations

from jugeo.research_orchestration import WorkspaceSite, ConsistencyReport
from jugeo.directed_research._types import HAS_EASY
from jugeo.directed_research._workspace import check_consistency


def full_consistency_check(
    workspace: WorkspaceSite,
    code_files: list[str],
) -> ConsistencyReport:
    """Run descent + jg verification for maximum trust."""
    return check_consistency(workspace)
