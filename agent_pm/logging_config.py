"""Rich logging configuration for the service."""

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler


def configure_logging(trace_dir: Path) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    console = Console()
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, markup=True, rich_tracebacks=True)],
    )


__all__ = ["configure_logging"]
