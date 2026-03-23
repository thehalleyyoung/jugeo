# copilot: jugeo cli sub-package — foundation pipeline command-line tools
"""CLI sub-package for the JuGeo foundation pipeline."""
from __future__ import annotations

__version__ = "0.1.0"


# --- auto-registered submodules ---
try:
    from . import cmd_alignment
except Exception:
    pass
try:
    from . import cmd_bugs
except Exception:
    pass
try:
    from . import cmd_classify
except Exception:
    pass
try:
    from . import cmd_encode
except Exception:
    pass
try:
    from . import cmd_equiv
except Exception:
    pass
try:
    from . import cmd_evaluate
except Exception:
    pass
try:
    from . import cmd_generate
except Exception:
    pass
try:
    from . import cmd_info
except Exception:
    pass
try:
    from . import cmd_load
except Exception:
    pass
try:
    from . import cmd_mixed
except Exception:
    pass
try:
    from . import cmd_repair
except Exception:
    pass
try:
    from . import cmd_run
except Exception:
    pass
try:
    from . import cmd_server
except Exception:
    pass
try:
    from . import cmd_spec
except Exception:
    pass
try:
    from . import cmd_test
except Exception:
    pass
try:
    from . import foundation_pipeline
except Exception:
    pass
try:
    from . import main
except Exception:
    pass
try:
    from . import novel_problem_codegen
except Exception:
    pass
