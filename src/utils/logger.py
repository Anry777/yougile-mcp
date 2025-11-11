"""
Logging configuration for YouGile MCP server.
Provides detailed logging for API calls, MCP operations, and debugging.
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output."""
    
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
    }
    RESET = '\033[0m'
    
    def format(self, record):
        if record.levelname in self.COLORS:
            record.levelname = f"{self.COLORS[record.levelname]}{record.levelname}{self.RESET}"
        return super().format(record)


def setup_logger(
    name: str = "yougile_mcp",
    log_level: str = "DEBUG",
    log_file: Optional[str] = None,
    console_output: bool = True
) -> logging.Logger:
    """
    Setup logger with file and console handlers.
    
    Args:
        name: Logger name
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Path to log file. If None, uses default location
        console_output: Whether to output to console
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Create logs directory if it doesn't exist
    if log_file is None:
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"yougile_mcp_{timestamp}.log"
    else:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
    
    # File handler - detailed format
    file_formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console handler - colored and simpler format
    if console_output:
        console_formatter = ColoredFormatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
    
    logger.info(f"Logger initialized. Log file: {log_file}")
    return logger


# Create default logger instance
_default_logger = None


def get_logger(name: str = "yougile_mcp") -> logging.Logger:
    """Get logger instance by name."""
    global _default_logger
    if _default_logger is None:
        _default_logger = setup_logger(name)
    return logging.getLogger(name)
