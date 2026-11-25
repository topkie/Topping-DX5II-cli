"""Exception types shared by the DX5 II package."""

from __future__ import annotations


class Dx5iiError(RuntimeError):
    """Raised for transport failures and unusable field values."""


class Dx5iiTimeout(Dx5iiError):
    """The device sent nothing back before the read deadline."""


__all__ = ["Dx5iiError", "Dx5iiTimeout"]
