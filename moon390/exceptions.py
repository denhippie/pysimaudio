"""Exceptions for the moon390 client library."""

from __future__ import annotations


class MoonError(Exception):
    """Base class for all moon390 errors."""


class MoonConnectionError(MoonError):
    """Raised when the TCP connection cannot be established or is lost."""


class MoonProtocolError(MoonError):
    """Raised when a received frame cannot be parsed."""


class MoonCommandError(MoonError):
    """Raised when the unit replies with an A1 error response.

    Attributes:
        offending_code: the command code the unit objected to (0 if HW/corrupt).
        error_code: the A1 param2 error code (see protocol.ERROR_CODES).
    """

    def __init__(self, offending_code: int, error_code: int) -> None:
        from .protocol import ERROR_CODES

        self.offending_code = offending_code
        self.error_code = error_code
        desc = ERROR_CODES.get(error_code, f"unknown error 0x{error_code:02X}")
        super().__init__(
            f"unit error 0x{error_code:02X} ({desc}) "
            f"for command 0x{offending_code:02X}"
        )
