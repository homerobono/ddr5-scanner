import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console(stderr=True)


def setup_logging(level: str = "INFO") -> logging.Logger:
    log_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()

    handler = RichHandler(
        console=console, rich_tracebacks=True, show_path=False
    )
    handler.setLevel(log_level)

    root.addHandler(handler)
    root.setLevel(log_level)

    # Silence noisy HTTP-level logging from httpx/httpcore
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    logger = logging.getLogger("ddr5-scanner")
    logger.setLevel(log_level)
    logger.propagate = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ddr5-scanner.{name}")
