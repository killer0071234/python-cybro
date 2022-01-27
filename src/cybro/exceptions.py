"""Exceptions for Cybro."""


class CybroError(Exception):
    """Generic Cybro exception."""


class CybroEmptyResponseError(Exception):
    """Cybro empty API response exception."""


class CybroConnectionError(CybroError):
    """Cybro connection exception."""


class CybroConnectionTimeoutError(CybroConnectionError):
    """Cybro connection Timeout exception."""
