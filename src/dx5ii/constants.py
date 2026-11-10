"""Command numbers, enumerations and device-wide constants for the DX5 II.

Every command number was recovered from the Topping Home web bundle
(``d34763bc9401c829.js`` / ``ff8462859a642010.js``), then verified on hardware.
Values are decimal-derived: ``28929 == 0x7101``.

The device speaks a 16-byte frame; see :mod:`dx5ii.protocol`.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# USB identity
# ---------------------------------------------------------------------------

VID: Final = 0x152A
PID: Final = 0x8750

#: The HID interface that carries the control protocol (``MI_02``).
PROTOCOL_INTERFACE: Final = 2

# ---------------------------------------------------------------------------
# protocol types
# ---------------------------------------------------------------------------

#: Request protocol type for query commands.
PT_READ: Final = 0x10
#: Request protocol type for write commands. The device always answers with
#: this value, whatever the request used.
PT_WRITE: Final = 0x20

# ---------------------------------------------------------------------------
# read / query commands
# ---------------------------------------------------------------------------

CMD_GET_SETTINGS: Final = 0x710C
CMD_GET_SAMPLING: Final = 0x710F
CMD_PEQ_PREVIEW: Final = 0x7137
CMD_AGREEMENT_CONFIG: Final = 0x1120

#: Starts the push session: the device reports vu/fft frames again.
CMD_CONNECT_STATE: Final = 0x1101

#: Reported inside the agreementConfig upload, not a standalone query.
CMD_CURRENT_CONFIG: Final = 0x1206
#: 78-word config block, one per EQ preset in the agreementConfig upload.
CMD_CONFIG_BLOCK: Final = 0x1106

#: Frames in the ``0x7137`` reply (meta / source hash / config hash).
PEQ_PREVIEW_FRAMES: Final = 3
#: The upload opens with two ``0x1106`` frames of ``totalFrameLen`` 2: word 0
#: is the number of preset blocks and word 1 is the ASCII magic ``TPDZ``.
CONFIG_HEADER_FRAMES: Final = 2
#: Safety cap on the block count an upload may announce.
MAX_CONFIG_BLOCKS: Final = 16

# ---------------------------------------------------------------------------
# EQ preset management
# ---------------------------------------------------------------------------

#: Delete preset {index}, then the caller re-selects a preset via 0x110E.
CMD_DELETE_CONFIG: Final = 0x110A
#: Upload a whole 78-word block to create a new preset (multi-frame).
CMD_DOWNLOAD_CONFIG: Final = 0x110B
#: Select which preset index is about to be renamed.
CMD_RENAME_CONFIG_INDEX: Final = 0x110C
#: Rename payload: 8 words of UTF-8 name (multi-frame).
CMD_RENAME_CONFIG_NAME: Final = 0x110D

CONFIG_BLOCK_WORDS: Final = 78
# Preamp words 5 and 7 of a preset block: fixed point, 0 dB = 2**25.
PREAMP_UNITY: Final = 0x02000000
PREAMP_MIN_DB: Final = -12.0
PREAMP_MAX_DB: Final = 12.0
#: The device only accepts 74 words on upload; the last four are padding.
CONFIG_UPLOAD_WORDS: Final = 74
CONFIG_HEAD_WORDS: Final = 8
BANDS_PER_CHANNEL: Final = 11
BAND_WORDS: Final = 3
#: Live-band write base: cmd = 0x9000 | (band+1) << 8 | (5*channel + param + 1).
CMD_PEQ_BAND_BASE: Final = 0x9000

#: Band type ids as stored in the config block.
BAND_TYPES: Final = {1: "PK", 2: "LP", 3: "HP", 4: "LS", 5: "HS"}
BAND_TYPE_IDS: Final = {v: k for k, v in BAND_TYPES.items() if v != "?"}

# ---------------------------------------------------------------------------
# write commands
# ---------------------------------------------------------------------------

CMD_POWER: Final = 0x7101
CMD_VOLUME: Final = 0x7102
CMD_MUTE: Final = 0x7103
CMD_INPUT: Final = 0x7104
CMD_OUTPUT: Final = 0x7105
CMD_BT_AUDIO: Final = 0x7106
CMD_BT_APTX: Final = 0x7107
CMD_REMOTE: Final = 0x7108
CMD_UAC: Final = 0x7109
CMD_BRIGHTNESS: Final = 0x710A
CMD_FACTORY_RESET: Final = 0x710B
CMD_PCM_FILTER: Final = 0x710D
CMD_LINE_MODE: Final = 0x710E
CMD_DISABLE_CTRL: Final = 0x7110
CMD_CALL_C1: Final = 0x7111
CMD_CALL_C2: Final = 0x7112
CMD_HOME_PAGE: Final = 0x7113
CMD_THEME: Final = 0x7114
CMD_POWER_TRIGGER: Final = 0x7115
CMD_BALANCE: Final = 0x7116
CMD_HEADPHONE_GAIN: Final = 0x7117
CMD_MULTIFUNCTION_KEY: Final = 0x7118
CMD_LANGUAGE: Final = 0x7119
CMD_INPUT_MODE: Final = 0x711A
CMD_INPUT_OPTION: Final = 0x711B
CMD_OUTPUT_OPTION: Final = 0x711C
CMD_POLARITY: Final = 0x711D
CMD_VOLUME_STEP: Final = 0x711E
CMD_VU_LEVEL: Final = 0x711F
CMD_VU_BAR_MODE: Final = 0x7120
CMD_VOLUME_MEMORY: Final = 0x7121
CMD_PEQ_MEMORY: Final = 0x7122
CMD_REMOTE_A_KEY: Final = 0x7123
CMD_REMOTE_B_KEY: Final = 0x7124
CMD_EXEC_REMOTE_A: Final = 0x7125
CMD_EXEC_REMOTE_B: Final = 0x7126
#: Write-only in practice; the firmware has no handler that accepts a name.
CMD_DEVICE_NAME: Final = 0x7127
CMD_SPDIF_MODE: Final = 0x7128
CMD_CROSSFEED_MEMORY: Final = 0x7129
CMD_DC_DETECT: Final = 0x712A
CMD_CROSSFEED_TYPE: Final = 0x712B
CMD_CROSSFEED_CONV: Final = 0x712C
CMD_CROSSFEED_SIMPLE: Final = 0x712D
CMD_PEQ_STATE: Final = 0x7132
CMD_HEARTBEAT: Final = 0x7134
CMD_SAVE_C1: Final = 0x7135
CMD_SAVE_C2: Final = 0x7136
CMD_BT_CLEAR_PAIRING: Final = 0x7139
CMD_SCREEN_TIMEOUT: Final = 0x713A
CMD_DIM_SCREEN_TYPE: Final = 0x713B
CMD_TRIGGER_OUT: Final = 0x713C
CMD_MULTIFUNCTION_DBL: Final = 0x713D
CMD_REMOTE_MUTE_MODE: Final = 0x713E

#: Pushed by the device after ``getSettings``.
CMD_PEQ_OPTION_MASK: Final = 0x713F
CMD_PCM_OPTION_MASK: Final = 0x7140
CMD_CROSSFEED_OPTION_MASK: Final = 0x7141
CMD_BLUETOOTH_STATE: Final = 0x7138

#: Switches the active EQ preset. ``0x1206`` only reports it.
CMD_SWITCH_MCU_CONFIG: Final = 0x110E

#: Backwards-compatible alias kept for callers that used the old name.
CMD_SET_OUTPUT: Final = CMD_OUTPUT
#: ``0x1206`` reports the active preset; writing it is ignored by the device.
CMD_SET_CURRENT_CONFIG: Final = CMD_CURRENT_CONFIG

#: PEQ preamp/enable block (``0x9C01``-``0x9C04``) -- not exposed as fields.
CMD_PEQ_PREAMP_BASE: Final = 0x9C01

# ---------------------------------------------------------------------------
# enumerations: device value -> UI id
# ---------------------------------------------------------------------------

INPUTS: Final = ["usb", "fiber", "coax", "bt"]
OUTPUTS: Final = [
    "all", "hp_all", "line_all", "hp_single", "hp_balanced",
    "line_single", "line_balanced",
]
PCM_FILTERS: Final = ["f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8"]
LINE_MODES: Final = ["preamp", "dac"]
UAC_MODES: Final = ["uac1", "uac2"]
BRIGHTNESS: Final = ["low", "medium", "high"]
LANGUAGES: Final = ["en", "zh", "ja", "zh_tw"]
INPUT_MODES: Final = ["auto", "manual"]
VOLUME_STEPS: Final = ["half_db", "one_db"]
POLARITIES: Final = ["normal", "reverse"]
MEMORY_MODES: Final = ["input", "output", "disabled"]
THEMES: Final = [
    "aurora", "orange", "peru", "pea_green", "dark_khaki",
    "rosy_brown", "blue", "purple_magic", "white",
]
HOME_PAGES: Final = ["normal", "vu", "fft"]
POWER_TRIGGERS: Final = ["signal", "trigger12v", "off"]
MULTIFUNCTION_KEYS: Final = [
    "input_select", "output_select", "home_select", "brightness",
    "screen_off", "mute", "peq_select", "power_trigger", "pcm_filter",
    "headphone_gain", "peq_toggle", "crossfeed_type", "crossfeed_config",
    "play_pause", "previous_track", "next_track",
]
SPDIF_MODES: Final = ["mode1", "mode2"]
VU_BAR_MODES: Final = ["all_on", "normal", "fft", "all_off"]
CLASSIC_VU_LEVELS: Final = ["plus4dbu", "plus10dbu"]
CROSSFEED_TYPES: Final = ["convolution", "simple", "off"]
CROSSFEED_CONV_OPTIONS: Final = ["c1", "c2"]
CROSSFEED_SIMPLE_OPTIONS: Final = ["s1", "s2", "s3", "s4"]
DC_DETECT_LEVELS: Final = ["low", "high"]
DIM_SCREEN_TYPES: Final = ["input_only", "all_black"]
TRIGGER_OUTS: Final = ["follow_power", "follow_lineout"]
REMOTE_MUTE_MODES: Final = ["device_mute", "input_adaptive"]
AUTO_SCREEN_TIMEOUTS: Final = ["off", "10s", "30s", "60s"]
BLUETOOTH_MODES: Final = ["off", "always", "input_only"]

#: Sample-rate code -> Hz, straight from the web bundle. Index 0 means "none".
SAMPLE_RATES: Final = [
    0, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000,
    705600, 768000, 2822400, 5644800, 11289600, 22579200, 45158400, 49152000,
]

#: ``screen-timeout`` accepts a label or a raw value; these are the raw values.
TIMEOUT_RAW: Final = {"off": 0, "10s": 10, "30s": 30, "60s": 60}

# ---------------------------------------------------------------------------
# settings word layout
# ---------------------------------------------------------------------------

#: ``(word index, field name, decoder)``. A decoder is either a scalar tag
#: (``"bool"``/``"int"``/``"int32"``/``"mask"``) or ``("enum", table)``.
SETTINGS_LAYOUT: Final = [
    (0, "powered", "bool"),
    (9, "volume_raw", "int"),
    (10, "muted", "bool"),
    (11, "input", ("enum", INPUTS)),
    (12, "output", ("enum", OUTPUTS)),
    (13, "high_gain", "bool"),
    (14, "home_page", ("enum", HOME_PAGES)),
    (15, "theme", ("enum", THEMES)),
    (16, "power_trigger", ("enum", POWER_TRIGGERS)),
    (17, "balance", "int32"),
    (18, "pcm_filter", ("enum", PCM_FILTERS)),
    (19, "line_mode", ("enum", LINE_MODES)),
    (20, "bluetooth_mode", ("enum", BLUETOOTH_MODES)),
    (21, "bluetooth_aptx", "bool"),
    (22, "remote_enabled", "bool"),
    (23, "multifunction_key", ("enum", MULTIFUNCTION_KEYS)),
    (24, "uac_mode", ("enum", UAC_MODES)),
    (25, "brightness", ("enum", BRIGHTNESS)),
    (26, "language", ("enum", LANGUAGES)),
    (27, "sample_rate_code", "int"),
    (28, "input_mode", ("enum", INPUT_MODES)),
    (29, "spdif_mode", ("enum", SPDIF_MODES)),
    (30, "input_option_mask", "mask"),
    (31, "output_option_mask", "mask"),
    (32, "volume_step", ("enum", VOLUME_STEPS)),
    (33, "polarity", ("enum", POLARITIES)),
    (34, "volume_memory", ("enum", MEMORY_MODES)),
    (35, "peq_memory", ("enum", MEMORY_MODES)),
    (36, "crossfeed_memory", ("enum", MEMORY_MODES)),
    (37, "dc_detect_sensitivity", "bool"),
    (38, "classic_vu_level", ("enum", CLASSIC_VU_LEVELS)),
    (39, "vu_bar_mode", ("enum", VU_BAR_MODES)),
    (40, "remote_a_key", ("enum", MULTIFUNCTION_KEYS)),
    (41, "remote_b_key", ("enum", MULTIFUNCTION_KEYS)),
    (42, "crossfeed_type", ("enum", CROSSFEED_TYPES)),
    (43, "crossfeed_conv_option_mask", "mask"),
    (44, "crossfeed_simple_option_mask", "mask"),
    (48, "auto_screen_timeout", ("enum", AUTO_SCREEN_TIMEOUTS)),
    (49, "dim_screen_type", ("enum", DIM_SCREEN_TYPES)),
    (50, "trigger_out", ("enum", TRIGGER_OUTS)),
    (51, "multifunction_double_key", ("enum", MULTIFUNCTION_KEYS)),
]

#: The device name occupies words 1..8: little-endian UTF-8, zero padded.
DEVICE_NAME_WORDS: Final = range(1, 9)

#: ``auto_screen_timeout`` is delivered as an enum index but read back raw.
AUTO_SCREEN_TIMEOUT_RAW: Final = {0: "off", 10: "10s", 30: "30s", 60: "60s"}

#: Minimum word count a ``getSettings`` reply must carry to be interpreted.
SETTINGS_MIN_WORDS: Final = 45
#: ``totalFrameLen`` of a healthy ``getSettings`` reply on firmware 2.58.
SETTINGS_FRAME_TOTAL: Final = 52

#: ``peqPreviewState`` source byte -> the configuration actually playing.
#:
#: Topping Home calls these ``none``/``cloud``/``audition``.  ``cloud`` is an
#: account config pushed by the web page and ``audition`` its in-memory
#: preview; neither replaces the local slot reported by ``0x1206``.  The byte
#: is zero while no preview is active.
PEQ_PREVIEW_SOURCES: Final = {0: "none", 1: "cloud", 2: "audition"}

#: Preview sources that are not one of the device's own preset slots.
PEQ_TEMP_MODES: Final = ("cloud", "audition")

#: Commands that change the device in ways the CLI cannot undo.
#:
#: ``firmwareUpdate`` puts the unit into flash mode and it stays there until a
#: firmware image is written; there is no command that brings it back.  A
#: factory reset wipes every user setting and preset.  ``raw`` refuses these
#: unless ``--force`` is given.
DESTRUCTIVE_COMMANDS: Final = {
    0x710B: "factory reset (erases all settings and presets)",
    0x712E: "firmware update mode (device leaves USB until reflashed)",
}
