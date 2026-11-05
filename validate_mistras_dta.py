"""Compare every hit or time-data column against an AEwin64 ASCII export."""

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
import math
from pathlib import Path
import re

from decode_easyae_dta import AEWIN_COLUMNS


# Export headings -> legacy keys. SIG STRNGTH is one heading, despite its space.
EXPORT_FIELDS = {heading.replace("SIG STRNGTH", "SIG_STRNGTH"): key
                 for heading, key in AEWIN_COLUMNS.items()}


def read_export(path):
    """Return headings and numeric text rows, preserving printed precision."""
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            columns = line.replace("SIG STRNGTH", "SIG_STRNGTH").split()
            if columns[:2] == ["ID", "SSSSSSSS.mmmuuun"]:
                break
        else:
            raise ValueError(f"No hit table header in {path}")
        if len(set(columns)) != len(columns):
            raise ValueError(f"Duplicate export headings in {path}")
        rows = []
        for line_number, line in enumerate(stream, 1):
            fields = line.split()
            if not fields:
                continue
            if len(fields) != len(columns):
                raise ValueError(f"Wrong export field count at data line {line_number} in {path}")
            rows.append(dict(zip(columns, fields)))
    return columns, rows


def reader_key(column):
    if column.startswith("PARA") and column[4:].isdigit():
        return "parametric_" + column[4:]
    if column not in EXPORT_FIELDS:
        raise ValueError(f"Unsupported export column {column!r}")
    return EXPORT_FIELDS[column]


def read_time_export(path):
    """Flatten AEwin64's timestamp / CH:[...] blocks into numeric text rows."""
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            columns = line.split()
            if columns[:2] == ["ID", "SSSSSSSS.mmmuuun"]:
                break
        else:
            raise ValueError(f"No time-data table header in {path}")
        if len(set(columns)) != len(columns) or "CH" in columns:
            raise ValueError(f"Invalid time-data headings in {path}")
        common, channel_definitions = None, None
        rows, channels = [], set()
        all_columns = list(columns)
        header_pending = False
        for line_number, line in enumerate(stream, 1):
            text = line.strip()
            if not text:
                continue
            if ":[" in text:
                blocks = list(re.finditer(r"(CH|\d+)\s*:\[([^\[\]]*)\]", text))
                remainder = re.sub(r"(CH|\d+)\s*:\[([^\[\]]*)\]", "", text)
                if common is None or not blocks or remainder.strip():
                    raise ValueError(f"Invalid time-data block at data line {line_number}")
                if all(block[1] == "CH" for block in blocks):
                    if header_pending:
                        raise ValueError(f"Invalid channel headings at data line {line_number}")
                    channel_definitions = []
                    for block in blocks:
                        fields = block[2].replace("SIG STRNGTH", "SIG_STRNGTH").split()
                        if not fields or len(set(fields)) != len(fields) or set(fields) & set(columns + ["CH"]):
                            raise ValueError(f"Invalid channel headings at data line {line_number}")
                        channel_definitions.append(fields)
                        for column in ["CH", *fields]:
                            if column not in all_columns:
                                all_columns.append(column)
                    header_pending = True
                else:
                    if channel_definitions is None or len(blocks) != len(channel_definitions):
                        raise ValueError(f"Invalid channel values at data line {line_number}")
                    for block, channel_columns in zip(blocks, channel_definitions):
                        fields = block[2].split()
                        if not block[1].isdigit() or len(fields) != len(channel_columns):
                            raise ValueError(f"Invalid channel values at data line {line_number}")
                        channel = int(block[1])
                        if channel in channels:
                            raise ValueError(f"Duplicate channel at data line {line_number}")
                        channels.add(channel)
                        rows.append({**common, "CH": str(channel), **dict(zip(channel_columns, fields))})
                    header_pending = False
            else:
                if header_pending:
                    raise ValueError(f"Missing channel values at data line {line_number}")
                if common is not None and not channels:
                    rows.append(common)
                fields = text.split()
                if len(fields) != len(columns):
                    raise ValueError(f"Wrong time-data field count at data line {line_number}")
                common = dict(zip(columns, fields))
                if common["ID"] not in ("2", "3"):
                    raise ValueError(f"Invalid time-data ID at data line {line_number}")
                channel_definitions, channels = None, set()
        if header_pending:
            raise ValueError(f"Missing final channel values in {path}")
        if common is not None and not channels:
            rows.append(common)
    return all_columns, rows


def compare_hits(path, hits):
    """Require identical columns/counts and agreement at each column's precision.

    Integer columns are exact. Decimal/scientific values allow half the last
    printed decimal place, plus floating point arithmetic error only.
    """
    columns, rows = read_export(path)
    return _compare_rows(columns, rows, hits, "hits")


def compare_time_data(path, data):
    """Compare all flattened channel rows at each exported value's precision."""
    columns, rows = read_time_export(path)
    return _compare_rows(columns, rows, data, "rows")


def _compare_rows(columns, rows, hits, count_key):
    legacy_keys = [reader_key(column) for column in columns]
    headings = [column.replace("SIG_STRNGTH", "SIG STRNGTH") for column in columns]
    column_set = set(columns)
    heading_set = set(headings)
    legacy_key_set = set(legacy_keys)
    if len(rows) != len(hits):
        raise ValueError(f"Export has {len(rows)} {count_key}; DTA has {len(hits)}")
    for index, (row, hit) in enumerate(zip(rows, hits), 1):
        if row.keys() != column_set:
            raise ValueError(f"Export column mismatch at row {index}")
        if hit.keys() == heading_set:
            keys = headings
        elif hit.keys() == legacy_key_set:
            keys = legacy_keys
        else:
            raise ValueError(f"Column mismatch at row {index}: "
                             f"export={sorted(headings)}, decoded={sorted(hit)}")
        for column, key in zip(columns, keys):
            token = row[column]
            try:
                expected = Decimal(token)
            except InvalidOperation as error:
                raise ValueError(f"Invalid export value {token!r} at row {index}") from error
            value = hit[key]
            if not expected.is_finite() or not math.isfinite(float(expected)) or not math.isfinite(value):
                raise ValueError(f"Non-finite {column} at row {index}")
            tolerance = (0.0 if expected.as_tuple().exponent == 0 and "." not in token
                         and "e" not in token.lower()
                         else float(Decimal(10) ** expected.as_tuple().exponent) / 2)
            epsilon = 4 * max(math.ulp(float(expected)), math.ulp(float(value)))
            if abs(float(expected) - value) > tolerance + epsilon:
                raise ValueError(f"{column} mismatch at row {index}: decoded {value}, "
                                 f"export {token}, tolerance {tolerance}")
    return {count_key: len(rows), "columns": columns, "values": len(rows) * len(columns)}


def corpus_paths(root):
    folders = (p for p in root.iterdir() if p.is_dir()
               and p.name.startswith("EMPIRE_acoustic_")
               and p.name.removeprefix("EMPIRE_acoustic_").isdigit())
    for folder in sorted(folders, key=lambda p: int(p.name.rsplit("_", 1)[1])):
        yield from sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".dta")


def validate_corpus(root, kind="hits"):
    from decode_easyae_dta import read_hits, read_time_data

    reader = read_hits if kind == "hits" else read_time_data
    compare = compare_hits if kind == "hits" else compare_time_data
    count_key = "hits" if kind == "hits" else "rows"
    compared_key = "compared_" + count_key
    totals = Counter({"files": 0, count_key: 0, "exports": 0, compared_key: 0, "compared_values": 0})
    records, errors = [], []
    for path in corpus_paths(root):
        record = {"file": path.relative_to(root).as_posix()}
        totals["files"] += 1
        try:
            hits = reader(path)
            totals[count_key] += len(hits)
            record[count_key] = len(hits)
            export = next((p for p in path.parent.iterdir() if p.is_file()
                           and p.name.lower() == path.stem.lower() + f"_{kind}.txt"), None)
            if export:
                result = compare(export, hits)
                record["comparison"] = result
                totals.update({"exports": 1, compared_key: result[count_key], "compared_values": result["values"]})
        except (OSError, ValueError) as error:
            errors.append({"file": record["file"], "error": str(error)})
        records.append(record)
    return {**totals, "comparison_policy": "All exported columns; exact integers; half last printed decimal place",
            "errors": errors, "files_detail": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="DTA file or EMPIRE corpus directory")
    parser.add_argument("--export", type=Path, help="Reference TXT for a single DTA")
    parser.add_argument("--kind", choices=("hits", "time"), default="hits", help="Data to compare (default: hits)")
    parser.add_argument("--output", type=Path, help="JSON report outside the raw data directory")
    args = parser.parse_args()
    if not args.path.exists():
        parser.error("path must exist")
    raw_root = args.path if args.path.is_dir() else args.path.parent
    if args.output and args.output.resolve().is_relative_to(raw_root.resolve()):
        parser.error("write the report outside the raw data directory")
    if args.path.is_file():
        from decode_easyae_dta import read_hits, read_time_data
        export = args.export or next((p for p in args.path.parent.iterdir() if p.is_file()
                                     and p.name.lower() == args.path.stem.lower() + f"_{args.kind}.txt"),
                                    args.path.with_name(args.path.stem + f"_{args.kind}.TXT"))
        reader = read_hits if args.kind == "hits" else read_time_data
        compare = compare_hits if args.kind == "hits" else compare_time_data
        report = compare(export, reader(args.path))
    else:
        if args.export:
            parser.error("--export requires a single DTA file")
        report = validate_corpus(args.path, args.kind)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "files_detail"}, indent=2))
    if report.get("errors") or ("files" in report and not report["files"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
