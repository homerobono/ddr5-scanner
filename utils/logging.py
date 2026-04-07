import logging
import sys

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )
    root = logging.getLogger("ddr5-scanner")
    root.handlers = []
    root.addHandler(
        RichHandler(console=console, rich_tracebacks=True, show_path=False)
    )
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return root


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ddr5-scanner.{name}")
