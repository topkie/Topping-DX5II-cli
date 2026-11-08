"""Exception types shared by the DX5 II package."""

from __future__ import annotations


class Dx5iiError(RuntimeError):
    """Raised for transport failures and unusable field values."""


__all__ = ["Dx5iiError"]
