"""Engine test setup: import the engine as the top-level package ``engine``.

Engine tests must run without Home Assistant installed. Importing the engine
through ``custom_components.better_home_ventilation`` would execute the
integration's ``__init__.py`` (which imports Home Assistant), so the
integration directory is put on ``sys.path`` instead.
"""

from pathlib import Path
import sys

INTEGRATION_DIR = (
    Path(__file__).resolve().parents[2]
    / "custom_components"
    / "better_home_ventilation"
)

if str(INTEGRATION_DIR) not in sys.path:
    sys.path.insert(0, str(INTEGRATION_DIR))
