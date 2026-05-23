import logging
import sys
from pathlib import Path
from app.core.config import settings

# Create logs directory if it doesn't exist
LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


class LogFormatter(logging.Formatter):
    """Custom formatter to style log messages for developer readability."""

    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    green = "\x1b[32;20m"
    cyan = "\x1b[36;20m"

    FORMATS = {
        logging.DEBUG: f"{grey}%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d){reset}",
        logging.INFO: f"{cyan}%(asctime)s{reset} - {green}%(name)s{reset} - %(levelname)s - %(message)s",
        logging.WARNING: f"{yellow}%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d){reset}",
        logging.ERROR: f"{red}%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d){reset}",
        logging.CRITICAL: f"{bold_red}%(asctime)s - %(name)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d){reset}"
    }

    def __init__(self, fmt: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"):
        super().__init__()
        self.fmt = fmt

    def format(self, record: logging.LogRecord) -> str:
        # Save original format to restore later
        orig_fmt = self._style._fmt

        # Replace format based on level
        log_fmt = self.FORMATS.get(record.levelno, self.fmt)
        self._style._fmt = log_fmt

        # Use parent class formatting
        result = super().format(record)

        # Restore original format
        self._style._fmt = orig_fmt
        return result


def setup_logging() -> None:
    """Configures system-wide logging handlers and levels."""
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    # Disable default handlers on root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    if sys.stdout.isatty():
        console_handler.setFormatter(LogFormatter())
    else:
        # Production style formatter (no ANSI escape codes)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
    console_handler.setLevel(log_level)

    # File handler for error logging
    file_handler = logging.FileHandler(LOGS_DIR / "app_errors.log", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s")
    )
    file_handler.setLevel(logging.ERROR)

    # Configure root logger
    root_logger.setLevel(log_level)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Set external libraries log levels to prevent spam
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    root_logger.info("Logging configured successfully.")
