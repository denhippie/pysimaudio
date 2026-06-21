"""State dataclasses and response decoders for the MOON Neo 390."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import protocol as P


@dataclass
class MediaInfo:
    """Now-playing metadata (MiND / Bluetooth)."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    genre: str | None = None
    image_url: str | None = None
    duration_s: int | None = None
    position_s: int | None = None
    source_tag: str | None = None  # 'M' (MiND) or 'B' (Bluetooth)


@dataclass
class MoonState:
    """The full known state of the unit, updated from responses/pushes."""

    available: bool = False
    powered: bool = False
    muted: bool = False
    dac_locked: bool = False
    display_off: bool = False
    psu_fault: bool = False
    dc_detected: bool = False

    volume_raw: int | None = None  # 0..800
    balance_raw: int | None = None  # 0..200 (100=center)
    input_id: int | None = None  # Scheme A
    sample_rate: str | None = None

    repeat: str = "none"  # 'none' | 'all' | 'one'
    shuffle: bool = False

    media: MediaInfo = field(default_factory=MediaInfo)

    serial: str | None = None
    product_id: int | None = None
    sw_rev: int | None = None
    comm_rev: int | None = None

    # Per-input setup, populated from A7: id -> (label, enabled).
    inputs: dict[int, "InputSetup"] = field(default_factory=dict)

    @property
    def volume_db(self) -> float | None:
        return None if self.volume_raw is None else self.volume_raw / 10

    @property
    def volume_level(self) -> float | None:
        """0.0..1.0 for Home Assistant."""
        if self.volume_raw is None:
            return None
        return self.volume_raw / P.VOLUME_MAX_RAW

    @property
    def input_name(self) -> str | None:
        if self.input_id is None:
            return None
        return P.INPUTS_SCHEME_A.get(self.input_id, f"input 0x{self.input_id:02X}")

    def source_list(self) -> list[str]:
        """Enabled inputs under their on-device labels (A7-driven).

        Falls back to the static Scheme-A map if no A7 data has arrived yet.
        """
        if self.inputs:
            return [s.display_name for s in self.inputs.values() if s.enabled]
        return list(P.INPUTS_SCHEME_A.values())


@dataclass
class InputSetup:
    """One input's configuration, from an A7 response."""

    input_id: int
    label: str
    enabled: bool
    offset_raw: int | None = None  # 0..200, 100 = 0.0 dB trim
    bypass: bool = False

    @property
    def display_name(self) -> str:
        label = self.label.strip()
        return label or P.INPUTS_SCHEME_A.get(
            self.input_id, f"input 0x{self.input_id:02X}"
        )


# --------------------------------------------------------------------------- #
# Decoders
# --------------------------------------------------------------------------- #
def parse_status(params: bytes) -> dict:
    """Decode an A3 UNIT status payload, length-defensively.

    The spec disagrees on A3's size (NN=08 with 3 bytes vs NN=10 with 7). We
    read whatever bytes are present and only fill the fields we actually got.
    Field order (per the 7-field layout): volMSB, volLSB, balance, inputId,
    sampleRate, state1, state2.
    """
    b = P.unhex_pairs(params)
    out: dict = {}

    if len(b) >= 2:
        out["volume_raw"] = b[0] * 256 + b[1]
    if len(b) >= 3:
        out["balance_raw"] = b[2]
    if len(b) >= 4:
        out["input_id"] = b[3]
    if len(b) >= 5:
        out["sample_rate"] = P.SAMPLE_RATES.get(b[4], f"0x{b[4]:02X}")
    if len(b) >= 6:
        s1 = b[5]
        out["powered"] = bool(s1 & 0x01)
        out["muted"] = bool(s1 & 0x02)
        out["dac_locked"] = bool(s1 & 0x04)
        out["display_off"] = bool(s1 & 0x08)
        out["psu_fault"] = bool(s1 & 0x10)
        out["dc_detected"] = bool(s1 & 0x20)
    if len(b) >= 7:
        s2 = b[6]
        if s2 & 0x01:
            out["repeat"] = "one"
        elif s2 & 0x02:
            out["repeat"] = "all"
        else:
            out["repeat"] = "none"
        out["shuffle"] = bool(s2 & 0x04)

    return out


def parse_input_setup(params: bytes) -> InputSetup:
    """Decode an A7 input-setup payload (best-effort, never raises).

    HARDWARE FINDING 2026-06-21 (confirmed via raw hex):
        A7 payload = id(2 hex ASCII) + label(literal ASCII)
    The label is literal ASCII (e.g. b"ANALOG"), NOT hex pairs. There is NO NUL
    terminator and NO trailer on this firmware -- the frame simply ends at the
    next '#'. Crucially there is therefore **no enabled/offset/bypass field**:
    A7 cannot tell us whether an input is enabled (see source_list design notes).

    A NUL is still honoured if a future firmware emits one, but `enabled` defaults
    to True because the wire gives us nothing to say otherwise.
    """
    if len(params) < 2:
        return InputSetup(input_id=0, label="", enabled=True)

    try:
        input_id = int(params[0:2].decode("ascii"), 16)
    except ValueError:
        return InputSetup(
            input_id=0, label=params.decode("ascii", "replace"), enabled=True
        )

    rest = params[2:]
    nul = rest.find(0x00)
    label = (rest if nul == -1 else rest[:nul]).decode("ascii", "replace")

    return InputSetup(
        input_id=input_id,
        label=label.strip(),
        enabled=True,  # A7 carries no enabled flag; cannot be inferred here
        offset_raw=None,
        bypass=False,
    )


def parse_product_info(params: bytes) -> dict:
    """Decode an A4 product-info payload: prodID + swRev + commRev."""
    b = P.unhex_pairs(params)
    out: dict = {}
    if len(b) >= 1:
        out["product_id"] = b[0]
    if len(b) >= 2:
        out["sw_rev"] = b[1]
    if len(b) >= 3:
        out["comm_rev"] = b[2]
    return out


def parse_media_text(params: bytes) -> tuple[str | None, str]:
    """Decode a media text push (AF/B0/B1/B2/B3/B4/B5).

    These are NOT hex-encoded: a 1-char source prefix ('M'/'B') followed by the
    literal ASCII text. Returns (source_tag, text).
    """
    if not params:
        return None, ""
    tag = chr(params[0])
    text = params[1:].decode("ascii", errors="replace")
    if tag in ("M", "B"):
        return tag, text
    # No recognised prefix -- treat the whole thing as text.
    return None, params.decode("ascii", errors="replace")


def parse_track_time(text: str) -> int | None:
    """Parse a 'M:SS' or 'H:MM:SS' time string to seconds."""
    parts = text.strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds
