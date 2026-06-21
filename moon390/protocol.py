"""Frame codec, command/response codes, and lookup tables for the MOON Neo 390.

All packets, both directions, have the form:

    #  <NN>  <CC>  [params...]  <CR>

where NN and CC are two ASCII hex chars each. NN counts the ASCII chars of the
packet EXCLUDING the leading '#', the trailing CR, and the NN field itself --
i.e. NN == len(code + params). It is always computed, never hardcoded.

See PROTOCOL_NOTES.md for the source spec and its flagged inconsistencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

HEADER = 0x23  # '#'
TERMINATOR = 0x0D  # CR
DEFAULT_PORT = 50000


# --------------------------------------------------------------------------- #
# Command / response codes
# --------------------------------------------------------------------------- #
class Cmd(IntEnum):
    """Command codes we send to the unit."""

    # Status / query (0x01-0x1F) -- all take no params unless noted
    GET_STATUS = 0x01  # -> A3
    GET_PRODUCT_INFO = 0x02  # -> A4
    GET_COMM_SETUP = 0x03  # -> A2
    GET_DISPLAY_STRING = 0x04  # -> A6
    GET_CD_INPUT = 0x0A  # -> AD
    GET_EXPANDED_INFO = 0x1F  # param = subsystem id (00=main) -> FE

    # Setup (0x20-0x5F)
    SET_COMM_PARAMS = 0x20  # baud + fb + dispFb -> A2
    RESET_UNIT = 0x21
    SET_FACTORY_DEFAULTS = 0x22
    SET_INPUT_LABEL = 0x23  # inputID + ASCII STR -> A7
    ENABLE_INPUT = 0x24  # inputID + BOOL -> A7
    SET_CD_INPUT = 0x2C  # inputID -> AD

    # User (0x60-0x9F)
    SET_POWER = 0x60  # 01 toggle / 02 on / 03 standby
    SET_DISPLAY = 0x61  # 01 toggle / 02 on / 03 off
    SET_DISPLAY_INTENSITY = 0x62  # 01 scroll / 02 low / 03 med / 04 high
    SET_INPUT = 0x63  # Scheme-A id (no swap), or 80 prev / 81 next
    SET_VOLUME = 0x64  # action + MSB + LSB
    SET_MUTE = 0x65  # 01 toggle / 02 on / 03 off
    SET_BALANCE = 0x66  # action + value
    PLAY = 0x67
    STOP = 0x68
    PAUSE = 0x69
    NEXT = 0x6A
    PREVIOUS = 0x6B
    SET_REPEAT = 0x6C  # 00 scroll / 01 none / 02 all / 03 one
    SET_RANDOM = 0x6D  # 00 scroll / 01 off / 02 on
    REQUEST_MEDIA_INFO = 0x6E  # 06 MiND / 07 Bluetooth


class Resp(IntEnum):
    """Response / push codes we receive from the unit (0xA0-0xFE)."""

    ACK = 0xA0
    ERROR = 0xA1
    COMM_SETUP = 0xA2
    STATUS = 0xA3
    PRODUCT_INFO = 0xA4
    DISPLAY_STRING_PUSH = 0xA5
    DISPLAY_STRING = 0xA6
    INPUT_SETUP = 0xA7
    FACTORY_DEFAULTS = 0xA9
    WAKEUP = 0xAA
    CD_SETUP = 0xAD
    SONG_NAME = 0xAF
    ARTIST_NAME = 0xB0
    ALBUM_NAME = 0xB1
    GENRE_NAME = 0xB2
    ALBUM_ART_URL = 0xB3
    TOTAL_TRACK_TIME = 0xB4
    TRACK_PLAYING_TIME = 0xB5
    EXPANDED_INFO = 0xFE


# --------------------------------------------------------------------------- #
# Action sub-codes
# --------------------------------------------------------------------------- #
class Power(IntEnum):
    TOGGLE = 0x01
    ON = 0x02
    STANDBY = 0x03


class OnOff(IntEnum):
    TOGGLE = 0x01
    ON = 0x02
    OFF = 0x03


class VolumeAction(IntEnum):
    DOWN_HALF = 0x01  # -0.5 dB
    DOWN_ONE = 0x02  # -1 dB
    UP_HALF = 0x04  # +0.5 dB
    UP_ONE = 0x05  # +1 dB
    SET = 0x07  # absolute, uses MSB/LSB


# --------------------------------------------------------------------------- #
# Input maps  (see PROTOCOL_NOTES.md S3 -- "READ THIS, it's a trap")
# --------------------------------------------------------------------------- #
# Scheme A: used for decode + display everywhere (A3 status, A7 setup, labels).
INPUTS_SCHEME_A: dict[int, str] = {
    0x01: "AES-EBU",
    0x02: "OPTICAL",
    0x03: "SPDIF",
    0x04: "USB",
    0x05: "MiND",
    0x06: "BLUETOOTH",
    0x07: "HDMI 1",
    0x08: "HDMI 2",
    0x09: "HDMI 3",
    0x0A: "HDMI 4",
    0x0B: "HDMI ARC",
    0x0C: "BALANCED",
    0x0D: "ANALOG",
    0x0E: "PHONO",
}

# There is ONE input scheme. The doc's §6.4 "Scheme B" (a BALANCED/ANALOG 0C/0D
# swap for the 0x63 select command) is WRONG -- HARDWARE FINDING 2026-06-21:
# 0x63 id 0x0C selects BALANCED and 0x0D selects ANALOG, matching A3/A7 (Scheme A)
# exactly. Appendix A was right; the body table was wrong. No swap anywhere.
_NAME_TO_ID = {name: i for i, name in INPUTS_SCHEME_A.items()}

# The MiND network input cannot be disabled (S4, command 0x24).
INPUT_MIND = 0x05


def select_input_id(name: str) -> int:
    """Return the input id for a canonical input name (single Scheme-A map)."""
    try:
        return _NAME_TO_ID[name]
    except KeyError:
        raise ValueError(f"unknown input name: {name!r}") from None


SAMPLE_RATES: dict[int, str] = {
    0x00: "none",
    0x01: "44.1 kHz",
    0x02: "48 kHz",
    0x03: "88.2 kHz",
    0x04: "96 kHz",
    0x05: "176.4 kHz",
    0x06: "192 kHz",
    0x07: "352.8 kHz",
    0x08: "384 kHz",
    0x09: "DSD64",
    0x0A: "DSD128",
    0x0B: "DSD256",
    0x0C: "DSD512",
    0x0D: "MQA 44.1 kHz",
    0x0E: "MQA 48 kHz",
    0x0F: "MQA 88.2 kHz",
    0x10: "MQA 96 kHz",
    0x11: "MQA 176.4 kHz",
    0x12: "MQA 192 kHz",
    0x13: "MQA 352.8 kHz",
    0x14: "MQA 384 kHz",
    0x15: "MQA Studio 44.1 kHz",
    0x16: "MQA Studio 48 kHz",
    0x17: "MQA Studio 88.2 kHz",
    0x18: "MQA Studio 96 kHz",
    0x19: "MQA Studio 176.4 kHz",
    0x1A: "MQA Studio 192 kHz",
    0x1B: "MQA Studio 352.8 kHz",
    0x1C: "MQA Studio 384 kHz",
}

ERROR_CODES: dict[int, str] = {
    0x01: "unknown command",
    0x02: "hardware interface error",
    0x03: "invalid parameter",
    0x04: "invalid/corrupted packet",
    0x05: "unit in standby",
    0x06: "unit in mute",
    0x07: "option not installed",
}

BAUD_RATES: dict[int, int] = {
    0x01: 38400,
    0x02: 19200,
    0x03: 9600,  # default
    0x04: 4800,
    0x05: 2400,
    0x06: 1200,
}

# Balance: 0x00 = full left, 0x64 = center, 0xC8 = full right.
BALANCE_CENTER = 0x64
BALANCE_MAX = 0xC8

# Volume: integer 0..800 == 0.0..80.0 dB (tenths of a dB).
VOLUME_MAX_RAW = 800


def encode_volume_raw(raw: int) -> tuple[int, int]:
    """Split a 0..800 volume into (MSB, LSB) for a 0x64 SET command."""
    if not 0 <= raw <= VOLUME_MAX_RAW:
        raise ValueError(f"volume out of range 0..{VOLUME_MAX_RAW}: {raw}")
    return raw // 256, raw % 256


def level_to_raw(level: float) -> int:
    """Map a Home Assistant 0.0..1.0 level to a 0..800 raw volume."""
    level = max(0.0, min(1.0, level))
    return round(level * VOLUME_MAX_RAW)


# --------------------------------------------------------------------------- #
# Hex helpers
# --------------------------------------------------------------------------- #
def hexbyte(value: int) -> bytes:
    """Encode one logical byte as two upper-case ASCII hex chars."""
    if not 0 <= value <= 0xFF:
        raise ValueError(f"byte out of range: {value}")
    return f"{value:02X}".encode("ascii")


def hexbytes(*values: int) -> bytes:
    """Encode a sequence of logical bytes as ASCII hex (concatenated)."""
    return b"".join(hexbyte(v) for v in values)


def unhex(data: bytes) -> int:
    """Decode an ASCII-hex byte string (any length) to an int. Case-insensitive."""
    return int(data.decode("ascii"), 16)


def unhex_pairs(data: bytes) -> list[int]:
    """Decode ASCII hex into a list of logical bytes (2 chars each)."""
    if len(data) % 2 != 0:
        raise MoonProtocolError(f"odd-length hex payload: {data!r}")
    return [int(data[i : i + 2], 16) for i in range(0, len(data), 2)]


# --------------------------------------------------------------------------- #
# Frame codec
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Frame:
    """A decoded packet: a command/response code plus its raw param bytes."""

    code: int
    params: bytes  # raw ASCII payload after the 2-char code, before CR

    @property
    def param_bytes(self) -> list[int]:
        """Params decoded as a list of logical (HEX) bytes."""
        return unhex_pairs(self.params)

    def __repr__(self) -> str:
        return f"Frame(code=0x{self.code:02X}, params={self.params!r})"


def build_frame(code: int, params: bytes = b"") -> bytes:
    """Build a complete wire frame for a command code and raw ASCII params.

    NN is computed as len(code_hex + params) == 2 + len(params).
    """
    body = f"{code:02X}".encode("ascii") + params
    nn = len(body)
    if nn > 0xFF:
        raise ValueError(f"frame too long: NN={nn}")
    return bytes([HEADER]) + f"{nn:02X}".encode("ascii") + body + bytes([TERMINATOR])


def build_command(code: int, *param_bytes: int) -> bytes:
    """Convenience: build a frame whose params are logical HEX bytes."""
    return build_frame(code, hexbytes(*param_bytes))


def iter_frames(buffer: bytearray) -> list[Frame]:
    """Pull all complete frames out of a streaming buffer (delimiter-driven).

    Frame on the wire: '#' + NN(2 hex) + CC(2 hex) + params + [CR].

    The frame is delimited by the **next '#' or CR**, NOT by NN. NN is unreliable
    on this hardware: A7 input-setup frames stream back-to-back with no CR and a
    FIXED bogus NN=0E regardless of label length (HARDWARE FINDING 2026-06-21,
    e.g. `#0EA70DANALOG#0EA70EPHONO`). Delimiter framing works for both the
    CR-terminated frames (A3/ACK/media) and the no-CR A7 bursts.

    Neither '#' (0x23) nor CR (0x0d) can appear inside a HEX payload (hex chars
    are 0-9A-F only), so the only theoretical ambiguity is a literal ASCII label
    containing '#' -- not worth handling unless we see it.

    A frame is only emitted once its terminator is seen, so a trailing frame with
    no following '#'/CR (e.g. the last A7 in a burst) stays buffered until more
    data arrives. Tolerates leading junk and partial reads.
    """
    frames: list[Frame] = []

    while True:
        start = buffer.find(HEADER)
        if start == -1:
            buffer.clear()  # no header anywhere -- discard junk
            break
        if start > 0:
            del buffer[:start]  # drop junk before the header

        next_hash = buffer.find(HEADER, 1)
        next_cr = buffer.find(TERMINATOR, 1)
        candidates = [x for x in (next_hash, next_cr) if x != -1]
        if not candidates:
            break  # no terminator yet; wait for more data

        end = min(candidates)
        body = bytes(buffer[1:end])  # between '#' and the delimiter (excl.)
        # Consume a CR terminator; leave a '#' in place (it starts the next frame).
        if buffer[end] == TERMINATOR:
            del buffer[: end + 1]
        else:
            del buffer[:end]

        if len(body) < 4:
            continue  # too short to hold NN(2) + code(2)
        try:
            code = int(body[2:4].decode("ascii"), 16)
        except ValueError:
            continue
        frames.append(Frame(code=code, params=body[4:]))

    return frames
