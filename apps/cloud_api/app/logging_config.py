"""Application-local logging configuration for allowlisted diagnostics."""

from __future__ import annotations

import logging

_FALLBACK_FORMAT = "%(levelname)s %(name)s %(message)s"


def configure_info_logger(logger: logging.Logger) -> logging.Logger:
    """Emit one application logger at INFO without changing the root logger."""
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        return logger

    uvicorn_handlers = logging.getLogger("uvicorn.error").handlers
    if uvicorn_handlers:
        for handler in uvicorn_handlers:
            logger.addHandler(handler)
        return logger

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FALLBACK_FORMAT))
    logger.addHandler(handler)
    return logger
