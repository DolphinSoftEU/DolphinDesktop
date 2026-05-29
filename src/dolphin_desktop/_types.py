from pathlib import Path
from typing import TypeAlias

ScreenshotPath: TypeAlias = str | Path | None
Timeout: TypeAlias = float
WaitState: TypeAlias = str  # e.g. "visible", "enabled", "exists", "ready"
