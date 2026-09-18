from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
import struct

DEVICE_NAME = "MEIT-BELT"

# Canonical strings corresponding to the current firmware BLE_UUID128_INIT
# byte arrays. Verify once on real hardware by listing GATT services.
SERVICE_UUID = "01000000-1d9e-218f-9a4b-9c4e302a9d11"
AUDIO_UUID   = "02000000-1d9e-218f-9a4b-9c4e302a9d11"
DIR_UUID     = "03000000-1d9e-218f-9a4b-9c4e302a9d11"
CMD_UUID     = "04000000-1d9e-218f-9a4b-9c4e302a9d11"

DIR_UNKNOWN = 0xFF
DIRECTION_NAMES = [
    "FRONT", "FRONT_RIGHT", "RIGHT", "BACK_RIGHT",
    "BACK", "BACK_LEFT", "LEFT", "FRONT_LEFT",
]

SOUND_CLASS_TO_ID = {"horn": 0, "siren": 1, "crash": 2}
SOUND_ID_TO_CLASS = {v: k for k, v in SOUND_CLASS_TO_ID.items()}
SOUND_CLASS_NONE = 0xFF

@dataclass(frozen=True)
class DirPacket:
    event_id: int
    direction: int
    confidence: float
    rms_dbfs: int

@dataclass(frozen=True)
class AudioChunk:
    event_id: int
    chunk_index: int
    last: bool
    pcm_bytes: bytes

@dataclass(frozen=True)
class CompletedAudio:
    event_id: int
    pcm_bytes: bytes
    lost: bool

def decode_dir_packet(data: bytes) -> DirPacket:
    if len(data) != 4:
        raise ValueError(f"DIR packet must be exactly 4 bytes, got {len(data)}")
    event_id = data[0]
    raw_dir = data[1]
    if raw_dir != DIR_UNKNOWN and not 0 <= raw_dir <= 7:
        raise ValueError(f"invalid direction byte: {raw_dir}")
    direction = -1 if raw_dir == DIR_UNKNOWN else raw_dir
    confidence = data[2] / 255.0
    rms_dbfs = struct.unpack("<b", data[3:4])[0]
    if direction == -1 and data[2] != 0:
        raise ValueError("DIR_UNKNOWN must carry confidence byte 0")
    return DirPacket(event_id, direction, confidence, rms_dbfs)

def decode_audio_chunk(data: bytes) -> AudioChunk:
    if len(data) < 3:
        raise ValueError(f"AUDIO chunk must be at least 3 bytes, got {len(data)}")
    pcm = bytes(data[3:])
    if len(pcm) % 2:
        raise ValueError("PCM16 payload must contain an even number of bytes")
    return AudioChunk(
        event_id=data[0],
        chunk_index=data[1],
        last=bool(data[2] & 0x01),
        pcm_bytes=pcm,
    )

class AudioAssembler:
    def __init__(self) -> None:
        self._events: Dict[int, dict] = {}

    def reset_event(self, event_id: int) -> None:
        self._events.pop(event_id, None)

    def push(self, chunk: AudioChunk) -> Optional[CompletedAudio]:
        st = self._events.get(chunk.event_id)
        if st is None:
            st = {"expected": 0, "parts": [], "lost": False}
            self._events[chunk.event_id] = st

        if chunk.chunk_index != st["expected"]:
            st["lost"] = True

        st["parts"].append(chunk.pcm_bytes)
        st["expected"] = (chunk.chunk_index + 1) & 0xFF

        if not chunk.last:
            return None

        out = CompletedAudio(
            event_id=chunk.event_id,
            pcm_bytes=b"".join(st["parts"]),
            lost=bool(st["lost"]),
        )
        self.reset_event(chunk.event_id)
        return out

def pcm16le_to_float32(pcm_bytes: bytes):
    import numpy as np
    if len(pcm_bytes) % 2:
        raise ValueError("PCM16 byte length must be even")
    return np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0

def _sound_class_id(sound_class) -> int:
    if isinstance(sound_class, int):
        if sound_class in (0, 1, 2, SOUND_CLASS_NONE):
            return sound_class
        raise ValueError(f"invalid sound_class id: {sound_class}")
    if str(sound_class) not in SOUND_CLASS_TO_ID:
        raise ValueError(f"unknown sound_class: {sound_class!r}")
    return SOUND_CLASS_TO_ID[str(sound_class)]

def encode_cmd(event_id: int, intensity: int, sound_class,
               pattern: Sequence[Sequence[int]]) -> bytes:
    if not 0 <= event_id <= 255:
        raise ValueError("event_id must fit uint8")
    if not 0 <= intensity <= 100:
        raise ValueError("intensity must be 0..100")
    if not 1 <= len(pattern) <= 4:
        raise ValueError("pattern must contain 1..4 pairs")

    out = bytearray([event_id, intensity, _sound_class_id(sound_class), len(pattern)])
    for pair in pattern:
        if len(pair) != 2:
            raise ValueError("each pattern entry must be [on_ms, off_ms]")
        on_ms, off_ms = int(pair[0]), int(pair[1])
        if on_ms < 0 or off_ms < 0:
            raise ValueError("pattern times must be non-negative")
        if on_ms % 10 or off_ms % 10:
            raise ValueError("pattern times must be multiples of 10 ms")
        if on_ms > 2550 or off_ms > 2550:
            raise ValueError("pattern time exceeds uint8*10 ms limit")
        out.append(on_ms // 10)
        out.append(off_ms // 10)
    return bytes(out)

def decode_cmd_packet(data: bytes) -> dict:
    if len(data) < 6:
        raise ValueError("CMD packet too short")
    event_id, intensity, sound_id, n_pairs = data[:4]
    expected = 4 + 2 * n_pairs
    if not 1 <= n_pairs <= 4 or len(data) != expected:
        raise ValueError("invalid CMD n_pairs/length")
    pattern: List[List[int]] = []
    for i in range(n_pairs):
        pattern.append([data[4 + 2*i] * 10, data[5 + 2*i] * 10])
    return {
        "event_id": event_id,
        "intensity": intensity,
        "sound_class_id": sound_id,
        "sound_class": SOUND_ID_TO_CLASS.get(sound_id, "none"),
        "pattern": pattern,
    }

def direction_name(direction: int) -> str:
    if direction == -1:
        return "UNKNOWN"
    if not 0 <= direction <= 7:
        raise ValueError("direction must be -1 or 0..7")
    return DIRECTION_NAMES[direction]
