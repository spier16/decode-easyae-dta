"""Native hit and time-data reader for the validated EasyAE V1.00 DTA profile.

See README.md for usage, physical units and supported-profile limits.
No third-party packages are required.
"""

from dataclasses import dataclass
import argparse
import csv
import math
from pathlib import Path
import struct
from typing import BinaryIO, Iterator, Optional

__version__ = "0.1.0"

__all__ = [
    "AEWIN_COLUMNS", "DtaFormatError", "DtaMetadata", "Message",
    "event_characteristics", "iter_hits", "iter_messages", "iter_time_data",
    "read_hits", "read_metadata", "read_time_data", "setup_subrecords",
    "write_hits_csv", "write_time_csv",
]


class DtaFormatError(ValueError):
    """A DTA message or setup record is incomplete or malformed."""


@dataclass(frozen=True)
class Message:
    offset: int
    payload: bytes  # Includes the ID and any extended header, excludes length.

    @property
    def id(self) -> int:
        return self.payload[0]


@dataclass(frozen=True)
class DtaMetadata:
    source_file: str
    product: Optional[str] = None
    version: Optional[str] = None
    test_start: Optional[str] = None  # Stored clock text; no timezone is supplied.


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
    result = []
    for subrecord in setup_subrecords(message):
        if subrecord[0] == 5:
            if len(subrecord) < 2 or len(subrecord) < 2 + subrecord[1]:
                raise DtaFormatError(f"Truncated characteristic list at byte {message.offset}")
            result.append(tuple(subrecord[2:2 + subrecord[1]]))
    return result


def setup_subrecords(message: Message) -> Iterator[bytes]:
    """Yield bounded hardware setup subrecords, including their sub-ID."""
    if message.id != 42:
        return
    payload = message.payload
    if len(payload) < 4 or payload[1] != 0:
        raise DtaFormatError(f"Invalid ID 42 header at byte {message.offset}")
    offset = 4
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise DtaFormatError(f"Truncated setup length at byte {message.offset}")
        length = int.from_bytes(payload[offset:offset + 2], "little")
        end = offset + 2 + length
        if length < 1 or end > len(payload):
            raise DtaFormatError(f"Invalid setup boundary at byte {message.offset}")
        subrecord = payload[offset + 2:end]
        yield subrecord
        offset = end


def _float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


# (reader key, storage, physical-unit multiplier). Order comes from ID 5.
_FEATURES = {
    1: ("rise_us", "H", 1), 2: ("counts_to_peak", "H", 1),
    3: ("counts", "H", 1), 4: ("energy", "H", 1),
    5: ("duration_us", "I", 1), 10: ("threshold_db", "B", 1),
    13: ("average_frequency_khz", "H", 1), 17: ("rms_v", "H", 1 / 5000),
    18: ("reverberation_frequency_khz", "H", 1),
    19: ("initiation_frequency_khz", "H", 1),
    20: ("signal_strength_pvs", "I", 3.05),
    21: ("absolute_energy_aj", "f", 0.000931),
    23: ("centroid_frequency_khz", "H", 1), 24: ("peak_frequency_khz", "H", 1),
    27: ("amplitude_db", "H", 0.1), 28: ("asl_db", "H", 0.1),
}

# ID 107's product-specific layout is undocumented in the installed appendix.
# Recognize the verified unity-scaling blob, and reject other settings.
_UNITY_SCALING = b"\x6b\x10\x27\x08\x00" + bytes.fromhex("00 00 00 00 00 e8 03 00 00 00 00") * 8
_PRODUCT = b")\x00\xbc\x02EasyAE (r) Location Version \n\rVersion V1.00\n\r\n\r\x1a\x00"


class _HitDecoder:
    def __init__(self):
        self.product = False
        self.scaling = False
        self.schema = None
        self.features = []
        self.max_parametrics = 0
        self.preamp = {}
        self.gain = {}
        self.calibration = set()

    def set_features(self, ids, offset):
        unknown = set(ids) - _FEATURES.keys()
        if unknown:
            raise DtaFormatError(f"Unsupported characteristic IDs {sorted(unknown)} at byte {offset}")
        if len(ids) != len(set(ids)):
            raise DtaFormatError(f"Duplicate characteristics at byte {offset}")
        self.features = [(ident, *_FEATURES[ident]) for ident in ids]
        self.schema = struct.Struct("<" + "".join(item[2] for item in self.features))

    def configure(self, payload, offset):
        ident = payload[0]
        if ident == 5:
            if len(payload) < 3 or len(payload) != 3 + payload[1]:
                raise DtaFormatError(f"Invalid event definition at byte {offset}")
            ids = payload[2:-1]
            self.set_features(ids, offset)
            self.max_parametrics = payload[-1]
            if self.max_parametrics > 8:
                raise DtaFormatError(f"Unsupported parametric count at byte {offset}")
        elif ident == 20:
            if len(payload) != 3 or payload[2] != 26:
                raise DtaFormatError(f"Unsupported preamp calibration at byte {offset}")
            self.preamp[payload[1]] = payload[2]
        elif ident == 23:
            if len(payload) != 4 or payload[2:] != b"\x00\x14":
                raise DtaFormatError(f"Unsupported gain/reference gain at byte {offset}")
            self.gain[payload[1]] = (payload[2], payload[3])
        elif ident == 176:
            if (len(payload) != 17 or payload[:4] != b"\xb0\x2a\x65\x00"
                    or payload[5:] != struct.pack("<dI", 1.0, 1000000)):
                raise DtaFormatError(f"Unsupported channel calibration at byte {offset}")
            self.calibration.add(payload[4])

    def require_setup(self, offset, channel=None):
        if not self.product or not self.scaling or self.schema is None:
            raise DtaFormatError(f"Missing supported EasyAE product, scaling or data setup at byte {offset}")
        if channel is not None and (channel not in self.preamp or channel not in self.gain
                                    or channel not in self.calibration):
            raise DtaFormatError(f"Missing calibration/gain for channel {channel} at byte {offset}")

    def decode(self, message):
        payload = message.payload
        if len(payload) < 8:
            raise DtaFormatError(f"Short hit at byte {message.offset}")
        channel = payload[7]
        self.require_setup(message.offset, channel)
        end = 8 + self.schema.size
        remaining = len(payload) - end
        if remaining < 0 or remaining % 3 or remaining // 3 > self.max_parametrics:
            raise DtaFormatError(f"Invalid hit payload length {len(payload)} at byte {message.offset}")
        hit = {"id": 1, "time_s": int.from_bytes(payload[1:7], "little") / 4000000,
               "channel": channel}
        hit.update(self.decode_features(payload, 8, message.offset))
        for start in range(end, len(payload), 3):
            pid = payload[start]
            key = f"parametric_{pid}"
            if not 1 <= pid <= 8 or key in hit:
                raise DtaFormatError(f"Unsupported/duplicate parametric ID {pid} at byte {message.offset}")
            # EasyAE specifies a 0-10 V, 16-bit input. ID 107 has unity scaling.
            hit[key] = int.from_bytes(payload[start + 1:start + 3], "little") * (10 / 65536)
        return hit

    def decode_features(self, payload, start, offset):
        values = {}
        for (ident, key, _, multiplier), raw in zip(self.features, self.schema.unpack_from(payload, start)):
            if not math.isfinite(raw) or raw < 0 or (ident in (5, 20) and raw > 0x7fffffff):
                raise DtaFormatError(f"Invalid or unsupported {key} value at byte {offset}")
            if ident in (20, 21):
                value = _float32(_float32(raw) * _float32(multiplier))
            elif ident in (27, 28):
                value = raw / 10
            else:
                value = raw * multiplier
            if not math.isfinite(value):
                raise DtaFormatError(f"Non-finite {key} at byte {offset}")
            values[key] = value
        return values


class _TimeDecoder(_HitDecoder):
    def __init__(self):
        super().__init__()
        self.parametric_ids = ()

    def configure(self, payload, offset):
        if payload[0] == 5:
            return  # Hit definitions do not change the time-data schema.
        if payload[0] != 6:
            return super().configure(payload, offset)
        if len(payload) < 3 or len(payload) < 3 + payload[1]:
            raise DtaFormatError(f"Truncated time-data definition at byte {offset}")
        end = 2 + payload[1]
        ids = payload[2:end]
        if set(ids) - {10, 17, 21, 28}:
            raise DtaFormatError(f"Unsupported time-data characteristics {list(ids)} at byte {offset}")
        if len(payload) != end + 1 + payload[end]:
            raise DtaFormatError(f"Invalid time parametric definition at byte {offset}")
        pids = tuple(payload[end + 1:])
        if len(pids) != len(set(pids)) or any(not 1 <= pid <= 8 for pid in pids):
            raise DtaFormatError(f"Unsupported/duplicate time parametric IDs at byte {offset}")
        self.set_features(ids, offset)
        self.parametric_ids = pids

    def decode(self, message):
        payload, offset = message.payload, message.offset
        self.require_setup(offset)
        end = 7 + 3 * len(self.parametric_ids)
        if len(payload) < end:
            raise DtaFormatError(f"Short time-data payload at byte {offset}")
        common = {"id": message.id, "time_s": int.from_bytes(payload[1:7], "little") / 4000000}
        for start, pid in zip(range(7, end, 3), self.parametric_ids):
            if payload[start] != pid:
                raise DtaFormatError(f"Time parametric ID differs from setup at byte {offset}")
            common[f"parametric_{pid}"] = int.from_bytes(payload[start + 1:start + 3], "little") * (10 / 65536)
        if not self.features:
            if len(payload) != end:
                raise DtaFormatError(f"Unexpected time-data channel bytes at byte {offset}")
            return [common]
        width = 1 + self.schema.size
        if len(payload) == end or (len(payload) - end) % width:
            raise DtaFormatError(f"Invalid time-data channel length at byte {offset}")
        rows, channels = [], set()
        for start in range(end, len(payload), width):
            channel = payload[start]
            if channel in channels:
                raise DtaFormatError(f"Duplicate time-data channel {channel} at byte {offset}")
            self.require_setup(offset, channel)
            channels.add(channel)
            rows.append({**common, "channel": channel,
                         **self.decode_features(payload, start + 1, offset)})
        return rows


def iter_hits(path):
    """Yield hit dictionaries in file order, in physical units.

    Unknown messages are skipped by their declared boundaries. Unsupported
    product/calibration/feature layouts raise DtaFormatError. A start marker
    is optional. Exhaust the iterator to validate the complete file.
    """
    yield from _iter_data(path, _HitDecoder(), (1,))


def _iter_data(path, decoder, message_ids):
    with Path(path).open("rb") as stream:
        for message in iter_messages(stream):
            if message.id == 41:
                if message.payload != _PRODUCT:
                    raise DtaFormatError(f"Unsupported product definition at byte {message.offset}")
                decoder.product = True
            elif message.id == 107:
                if message.payload != _UNITY_SCALING:
                    raise DtaFormatError(f"Unsupported parametric scaling at byte {message.offset}")
                decoder.scaling = True
            elif message.id == 42:
                if message.payload[2:4] != b"\x67\x00":
                    raise DtaFormatError(f"Unsupported hardware setup version at byte {message.offset}")
                for payload in setup_subrecords(message):
                    decoder.configure(payload, message.offset)
            elif message.id in (5, 6, 20, 23, 176):
                decoder.configure(message.payload, message.offset)
            elif message.id == 8:
                raise DtaFormatError(f"Continued-file setup is unsupported at byte {message.offset}")
            elif message.id in message_ids:
                decoded = decoder.decode(message)
                if isinstance(decoder, _TimeDecoder):
                    yield from decoded
                else:
                    yield decoded
        decoder.require_setup(stream.tell())


def read_hits(path):
    """Return all decoded hit dictionaries; see iter_hits for streaming reads."""
    return list(iter_hits(path))


def iter_time_data(path):
    """Yield time-driven (ID 2) and forced-sample (ID 3) rows in file order.

    Each channel becomes a row, repeating its timestamp and parametric values.
    Parametric-only records have no channel field. Exhaust to validate the file.
    """
    yield from _iter_data(path, _TimeDecoder(), (2, 3))


def read_time_data(path):
    """Return all time-data rows; see iter_time_data for streaming reads."""
    return list(iter_time_data(path))


def _metadata_text(message, start):
    try:
        text = message.payload[start:].rstrip(b"\x00\x1a\r\n ").decode("ascii")
    except UnicodeDecodeError as error:
        raise DtaFormatError(f"Non-ASCII metadata in ID {message.id} at byte {message.offset}") from error
    if not text.strip() or "\x00" in text:
        raise DtaFormatError(f"Invalid metadata in ID {message.id} at byte {message.offset}")
    return text


def read_metadata(path):
    """Read product (ID 41) and test-start text (ID 99) from the DTA.

    The source path is the supplied file's resolved location. Missing fields
    remain None; filesystem dates are never used as recording timestamps.
    """
    path = Path(path)
    product = version = test_start = None
    with path.open("rb") as stream:
        for message in iter_messages(stream):
            if message.id == 41:
                if len(message.payload) < 4 or message.payload[1] != 0:
                    raise DtaFormatError(f"Invalid ID 41 header at byte {message.offset}")
                lines = [line.strip() for line in _metadata_text(message, 4).splitlines() if line.strip()]
                new_product = lines[0]
                new_version = next((line.removeprefix("Version ") for line in lines[1:]
                                    if line.startswith("Version ")), None)
                if product is not None and (product, version) != (new_product, new_version):
                    raise DtaFormatError(f"Conflicting product metadata at byte {message.offset}")
                product, version = new_product, new_version
            elif message.id == 99:
                text = _metadata_text(message, 1)
                if len(text.splitlines()) != 1:
                    raise DtaFormatError(f"Invalid test-start text at byte {message.offset}")
                if test_start is not None and test_start != text:
                    raise DtaFormatError(f"Conflicting test-start records at byte {message.offset}")
                test_start = text
    return DtaMetadata(str(path.resolve()), product, version, test_start)


# AEwin64's ASCII export order; only columns present in the hits are written.
AEWIN_COLUMNS = {
    "ID": "id", "SSSSSSSS.mmmuuun": "time_s",
    **{f"PARA{pid}": f"parametric_{pid}" for pid in range(1, 9)},
    "CH": "channel", "RISE": "rise_us", "COUN": "counts", "ENER": "energy",
    "DURATION": "duration_us", "A-FRQ": "average_frequency_khz", "RMS": "rms_v",
    "PCNTS": "counts_to_peak", "THR": "threshold_db",
    "R-FRQ": "reverberation_frequency_khz", "I-FRQ": "initiation_frequency_khz",
    "SIG STRNGTH": "signal_strength_pvs", "ABS-ENERGY": "absolute_energy_aj",
    "FRQ-C": "centroid_frequency_khz", "P-FRQ": "peak_frequency_khz",
    "AMP": "amplitude_db", "ASL": "asl_db",
}


def write_hits_csv(hits, stream, *, metadata=None):
    """Write AEwin64 hit columns, optionally preceded by recording metadata."""
    _write_data_csv(hits, stream, metadata=metadata, empty_fields={"id", "time_s", "channel"})


def write_time_csv(rows, stream, *, metadata=None):
    """Write a regular time-data table using AEwin64 column names."""
    _write_data_csv(rows, stream, metadata=metadata, empty_fields={"id", "time_s"})


def _write_data_csv(hits, stream, *, metadata, empty_fields):
    hits = list(hits)
    fields = {key for hit in hits for key in hit} or empty_fields
    unknown = fields - set(AEWIN_COLUMNS.values())
    if unknown:
        raise ValueError(f"Unsupported CSV fields: {sorted(unknown)}")
    columns = [(heading, key) for heading, key in AEWIN_COLUMNS.items() if key in fields]
    writer = csv.writer(stream)
    if metadata is not None:
        writer.writerows([
            ["Source File", metadata.source_file],
            ["Product", metadata.product or "Not recorded"],
            ["Version", metadata.version or "Not recorded"],
            ["Test Start", metadata.test_start or "Not recorded"],
            [],
        ])
    writer.writerow([heading for heading, _ in columns])
    writer.writerows([hit.get(key, "") for _, key in columns] for hit in hits)


def main():
    parser = argparse.ArgumentParser(description="Extract EasyAE DTA hits or time data to CSV")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("path", type=Path, help="Input DTA file")
    parser.add_argument("--output", type=Path, required=True, help="New CSV file (must not exist)")
    parser.add_argument("--kind", choices=("hits", "time"), default="hits", help="Data to extract (default: hits)")
    parser.add_argument("--no-metadata", action="store_true", help="Write only the data table, without the metadata preamble")
    args = parser.parse_args()
    if args.output.suffix.lower() != ".csv":
        parser.error("output must have a .csv extension")
    if args.output.resolve() == args.path.resolve():
        parser.error("output must differ from the input")
    try:
        # Validate the entire input before opening an output file.
        hits = read_hits(args.path) if args.kind == "hits" else read_time_data(args.path)
        metadata = None if args.no_metadata else read_metadata(args.path)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="") as stream:
            writer = write_hits_csv if args.kind == "hits" else write_time_csv
            writer(hits, stream, metadata=metadata)
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")
    label = "hits" if args.kind == "hits" else "time rows"
    print(f"Extracted {len(hits)} {label} to {args.output}")


if __name__ == "__main__":
    main()
