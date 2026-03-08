"""Thread-safe logging utilities with split console/file levels."""
import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional
from logging.handlers import RotatingFileHandler
import contextvars


_cv_agent_id: contextvars.ContextVar[str] = contextvars.ContextVar('agent_id', default='N/A')
_cv_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar('agent_name', default='N/A')


class AgentContextFilter(logging.Filter):
    """Injects agent context into log records."""

    def filter(self, record):
        record.agent_id = _cv_agent_id.get()
        record.agent_name = _cv_agent_name.get()
        return True


class TruncateFilter(logging.Filter):
    """Truncate excessively long console messages.

    Full messages are preserved in the file handler; only the console sees
    the trimmed version.
    """

    MAX_LEN = 500

    def filter(self, record):
        if len(record.getMessage()) > self.MAX_LEN:
            record.msg = str(record.msg)[:self.MAX_LEN] + ' … [truncated]'
            record.args = None
        return True


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per line (JSONL) for machine-readable logs."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            'ts': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'agent': getattr(record, 'agent_name', 'N/A'),
            'agent_id': getattr(record, 'agent_id', 'N/A'),
            'msg': record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            obj['exc'] = self.formatException(record.exc_info)
        return json.dumps(obj, ensure_ascii=False)


class Logger:
    """Thread-safe singleton logger with split console/file levels."""

    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not Logger._initialized:
            self._setup_logger()
            Logger._initialized = True

    def _setup_logger(
        self,
        log_dir: Optional[str] = None,
        console_level: int = logging.WARNING,
        file_level: int = logging.DEBUG,
    ):
        """Configure the logging system.

        Parameters
        ----------
        log_dir : str, optional
            Directory for rotating log files.
        console_level : int
            Minimum level shown on the console (default WARNING).
        file_level : int
            Minimum level written to files (default DEBUG).
        """
        self.logger = logging.getLogger('finsight')
        self.logger.setLevel(logging.DEBUG)  # root captures everything

        if self.logger.handlers:
            return

        context_filter = AgentContextFilter()
        _console_fmt = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(agent_name)s:%(agent_id)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        # --- Console handler (human-readable, level-gated) ---
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(_console_fmt)
        console_handler.addFilter(context_filter)
        console_handler.addFilter(TruncateFilter())
        self.logger.addHandler(console_handler)
        self._console_handler = console_handler

        if log_dir:
            self._add_file_handlers(log_dir, file_level, context_filter)

    def _add_file_handlers(
        self,
        log_dir: str,
        file_level: int,
        context_filter: logging.Filter,
    ) -> None:
        """Add rotating text + JSONL file handlers (idempotent)."""
        os.makedirs(log_dir, exist_ok=True)

        _detailed_fmt = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(agent_name)s:%(agent_id)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )

        # --- Rotating text log ---
        text_path = os.path.join(log_dir, 'finsight.log')
        if not self._has_handler_for(text_path):
            fh = RotatingFileHandler(
                text_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8',
            )
            fh.setLevel(file_level)
            fh.setFormatter(_detailed_fmt)
            fh.addFilter(context_filter)
            self.logger.addHandler(fh)

        # --- JSONL structured log ---
        jsonl_path = os.path.join(log_dir, 'finsight.jsonl')
        if not self._has_handler_for(jsonl_path):
            jh = RotatingFileHandler(
                jsonl_path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding='utf-8',
            )
            jh.setLevel(file_level)
            jh.setFormatter(_JsonFormatter(datefmt='%Y-%m-%dT%H:%M:%S'))
            jh.addFilter(context_filter)
            self.logger.addHandler(jh)

    def _has_handler_for(self, filepath: str) -> bool:
        abs_path = os.path.abspath(filepath)
        for h in self.logger.handlers:
            if isinstance(h, RotatingFileHandler):
                if os.path.abspath(h.baseFilename) == abs_path:
                    return True
        return False

    def set_log_dir(self, log_dir: str):
        """Configure the directory used for log files."""
        if log_dir:
            context_filter = AgentContextFilter()
            self._add_file_handlers(log_dir, logging.DEBUG, context_filter)

    def set_console_level(self, level: int) -> None:
        """Change the console handler's level at runtime."""
        if hasattr(self, '_console_handler'):
            self._console_handler.setLevel(level)

    def set_agent_context(self, agent_id: str, agent_name: str):
        """Set the agent identifiers for the current async context."""
        _cv_agent_id.set(agent_id)
        _cv_agent_name.set(agent_name)

    def clear_agent_context(self):
        """Reset the agent identifiers for the current async context (restore to N/A)."""
        _cv_agent_id.set('N/A')
        _cv_agent_name.set('N/A')

    # ----- convenience log methods -----

    def debug(self, message: str):
        self.logger.debug(message)

    def info(self, message: str):
        self.logger.info(message)

    def warning(self, message: str):
        self.logger.warning(message)

    def error(self, message: str, exc_info: bool = False):
        self.logger.error(message, exc_info=exc_info)

    def exception(self, message: str):
        self.logger.exception(message)

    def critical(self, message: str):
        self.logger.critical(message)

    def addHandler(self, handler):
        self.logger.addHandler(handler)

    def removeHandler(self, handler):
        self.logger.removeHandler(handler)


# Global logger singleton
_logger_instance = None


def get_logger() -> Logger:
    """Return the global logger instance."""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = Logger()
    return _logger_instance


def setup_logger(
    log_dir: Optional[str] = None,
    log_level: int = logging.INFO,
    console_level: Optional[int] = None,
    file_level: int = logging.DEBUG,
    verbose: bool = False,
    quiet: bool = False,
) -> Logger:
    """Configure and return the logger instance.

    Parameters
    ----------
    log_dir : str, optional
        Directory for log files.
    log_level : int
        Legacy parameter — used as console_level when *console_level* is None.
    console_level : int, optional
        Explicit console level (overrides *log_level* and verbose/quiet).
    file_level : int
        File handler level (default DEBUG).
    verbose : bool
        If True, set console to DEBUG.
    quiet : bool
        If True, set console to ERROR.
    """
    logger = get_logger()

    # Determine effective console level
    if console_level is not None:
        effective_console = console_level
    elif verbose:
        effective_console = logging.DEBUG
    elif quiet:
        effective_console = logging.ERROR
    else:
        effective_console = log_level

    logger._setup_logger(
        log_dir=log_dir,
        console_level=effective_console,
        file_level=file_level,
    )
    if log_dir:
        logger.set_log_dir(log_dir)
    logger.set_console_level(effective_console)
    return logger


# Convenience wrappers for the global logger
def debug(message: str):
    get_logger().debug(message)


def info(message: str):
    get_logger().info(message)


def warning(message: str):
    get_logger().warning(message)


def error(message: str, exc_info: bool = False):
    get_logger().error(message, exc_info=exc_info)


def exception(message: str):
    get_logger().exception(message)


def critical(message: str):
    get_logger().critical(message)
