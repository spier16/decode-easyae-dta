"""Length-delimited MISTRAS DTA records, without guessing feature encodings."""

from dataclasses import dataclass
from typing import BinaryIO, Iterator


class DtaFormatError(ValueError):
    """A DTA message or setup record is incomplete or malformed."""


@dataclass(frozen=True)
class Message:
    offset: int
    payload: bytes  # Includes the ID and any extended header, excludes length.

    @property
    def id(self) -> int:
        return self.payload[0]


def iter_messages(stream: BinaryIO) -> Iterator[Message]:
    """Read complete messages so unrecognized fields cannot shift the next ID.

    Length is a little-endian unsigned 16-bit count of the payload bytes.
    Unknown IDs and trailing bytes remain available in ``payload``.
    """
    offset = 0
    while True:
        length_bytes = stream.read(2)
        if not length_bytes:
            return
        if len(length_bytes) != 2:
            raise DtaFormatError(f"Truncated length at byte {offset}")
        length = int.from_bytes(length_bytes, "little")
        if length == 0:
            raise DtaFormatError(f"Empty message at byte {offset}")
        payload = stream.read(length)
        if len(payload) != length:
            raise DtaFormatError(
                f"Truncated message at byte {offset}: expected {length} payload "
                f"bytes, got {len(payload)}"
            )
        yield Message(offset, payload)
        offset += 2 + length


def event_characteristics(message: Message) -> list[tuple[int, ...]]:
    """Extract ordered ID lists from ID 42 / sub-ID 5 setup records.

    Other setup records are skipped by their declared lengths. The extended
    ID and two-byte version occupy the first four bytes of message 42.
    """
    if message.id != 42:
        return []
    payload = message.payload
    if len(payload) < 4 or payload[1] != 0:
        raise DtaFormatError(f"Invalid ID 42 header at byte {message.offset}")
    result = []
    offset = 4
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise DtaFormatError(f"Truncated setup length at byte {message.offset}")
        length = int.from_bytes(payload[offset:offset + 2], "little")
        end = offset + 2 + length
        if length < 1 or end > len(payload):
            raise DtaFormatError(f"Invalid setup boundary at byte {message.offset}")
        subrecord = payload[offset + 2:end]
        if subrecord[0] == 5:
            if len(subrecord) < 2 or len(subrecord) < 2 + subrecord[1]:
                raise DtaFormatError(f"Truncated characteristic list at byte {message.offset}")
            result.append(tuple(subrecord[2:2 + subrecord[1]]))
        offset = end
    return result
