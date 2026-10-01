"""User-safe error type: technical detail is logged, never rendered raw."""

from __future__ import annotations

import logging

from platform_core.status import Status


LOGGER = logging.getLogger("platform_core")


class PlatformError(Exception):
    """An expected failure with a safe message and an explicit status code."""

    def __init__(self, message: str, status: Status = Status.DATA_UNAVAILABLE,
                 detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.detail = dict(detail or {})

    def log(self, context: str) -> "PlatformError":
        LOGGER.warning("%s failed [%s]: %s | detail=%s", context, self.status.value,
                       self.message, self.detail)
        return self
