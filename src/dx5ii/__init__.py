"""Control a Topping DX5 II over its HID interface.

Public API::

    from dx5ii import Dx5ii, Dx5iiError, apply_changes

    with Dx5ii() as dev:
        state = dev.snapshot()                 # full read
        dev.set_output("hp_balanced")          # switch output port
        dev.set_config_index(2)                # switch EQ preset

Everything the CLI does is available here; the CLI is a thin wrapper around
:class:`Dx5ii`, :func:`dx5ii.fields.encode_field` and
:func:`dx5ii.actions.apply_changes`.
"""

from __future__ import annotations

from .actions import apply_changes, state_summary
from .bands import (
    Band,
    decode_block,
    encode_block,
    encode_name_words,
    parse_apo_filters,
)
from .constants import (
    PID,
    PROTOCOL_INTERFACE,
    PT_READ,
    PT_WRITE,
    VID,
)
from .device import Dx5ii, list_interfaces
from .errors import Dx5iiError
from .fields import (
    FIELD_ACTIONS,
    FIELD_SPECS,
    encode_field,
    field_raw,
    field_state,
    resolve_config,
    resolve_output,
)
from .interpreter import (
    collect_words,
    interpret_config_blocks,
    interpret_config_upload,
    interpret_peq_preview,
    interpret_sampling,
    interpret_settings,
    resolve_playing,
)
from .peq import (
    create_preset,
    delete_preset,
    find_preset,
    read_presets,
    rename_preset,
)
from .protocol import Frame, build_frame, db_to_volume, decode_name, format_firmware, mask_to_items, parse_frame, volume_to_db

__version__ = "0.0.1"

__all__ = [
    "__version__",
    # identity
    "VID",
    "PID",
    "PROTOCOL_INTERFACE",
    "PT_READ",
    "PT_WRITE",
    # device
    "Dx5ii",
    "Dx5iiError",
    "list_interfaces",
    # actions
    "apply_changes",
    "state_summary",
    # fields
    "FIELD_SPECS",
    "FIELD_ACTIONS",
    "encode_field",
    "field_state",
    "field_raw",
    "resolve_output",
    "resolve_config",
    # EQ presets
    "Band",
    "decode_block",
    "encode_block",
    "encode_name_words",
    "parse_apo_filters",
    "read_presets",
    "find_preset",
    "create_preset",
    "delete_preset",
    "rename_preset",
    # protocol
    "Frame",
    "build_frame",
    "parse_frame",
    "decode_name",
    "volume_to_db",
    "format_firmware",
    "db_to_volume",
    "mask_to_items",
    # interpreters
    "collect_words",
    "interpret_settings",
    "interpret_sampling",
    "interpret_peq_preview",
    "interpret_config_upload",
    "interpret_config_blocks",
    "resolve_playing",
]
