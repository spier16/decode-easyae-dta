"""Survey numbered EasyAE folders and check existing hit exports, read-only."""

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path

from decode_easyae_dta import DtaFormatError, event_characteristics, iter_messages


def compare_export(path, binary_hits):
    """Compare count, relative time and channel; do not claim feature decoding."""
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            columns = line.split()
            if columns[:2] == ["ID", "SSSSSSSS.mmmuuun"]:
                channel_column = columns.index("CH")
                break
        else:
            raise ValueError(f"No hit table header in {path.name}")
        hits = []
        for line in stream:
            fields = line.split()
            if fields:
                hits.append((float(fields[1]), int(fields[channel_column])))
    if len(hits) != len(binary_hits):
        raise ValueError(f"Export has {len(hits)} hits; DTA has {len(binary_hits)}")
    for row, ((time, channel), (ticks, binary_channel)) in enumerate(zip(hits, binary_hits), 1):
        if not math.isfinite(time) or channel != binary_channel or abs(time - ticks * 0.25e-6) > 5.1e-8:
            raise ValueError(f"Export time/channel mismatch at hit {row}")
    return len(hits)


def survey(root):
    folders = sorted(
        (p for p in root.iterdir() if p.is_dir()
         and p.name.startswith("EMPIRE_acoustic_")
         and p.name.removeprefix("EMPIRE_acoustic_").isdigit()),
        key=lambda p: int(p.name.rsplit("_", 1)[1]),
    )
    profiles = defaultdict(list)
    totals = Counter()
    records = []
    errors = []
    missing = []
    for folder in folders:
        paths = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".dta")
        if not paths:
            missing.append(folder.name)
        for path in paths:
            relative = path.relative_to(root).as_posix()
            counts = Counter()
            hit_lengths = Counter()
            control_lengths = Counter()
            schemas = []
            binary_hits = []
            record = {"file": relative, "bytes": path.stat().st_size}
            try:
                with path.open("rb") as stream:
                    for message in iter_messages(stream):
                        counts[message.id] += 1
                        schemas.extend(event_characteristics(message))
                        if message.id == 1:
                            if len(message.payload) < 8:
                                raise DtaFormatError(f"Short hit at byte {message.offset}")
                            binary_hits.append((int.from_bytes(message.payload[1:7], "little"), message.payload[7]))
                            hit_lengths[len(message.payload)] += 1
                        if message.id in (128, 129, 130):
                            control_lengths[f"{message.id}:{len(message.payload)}"] += 1
                profile = json.dumps({"characteristics": schemas, "hit_payload_lengths": sorted(hit_lengths)})
                profiles[profile].append(relative)
                totals.update(counts)
                record.update({"message_counts": dict(sorted(counts.items())),
                               "characteristics": schemas,
                               "hit_payload_lengths": dict(hit_lengths),
                               "control_payload_lengths": dict(control_lengths)})
                export = next((p for p in path.parent.iterdir() if p.is_file()
                               and p.name.lower() == path.stem.lower() + "_hits.txt"), None)
                if export:
                    record["export_hits_verified"] = compare_export(export, binary_hits)
            except (OSError, ValueError) as error:
                errors.append({"file": relative, "error": str(error)})
            records.append(record)
    return {"numbered_folders": len(folders), "dta_files": len(records),
            "total_bytes": sum(r["bytes"] for r in records),
            "message_counts": dict(sorted(totals.items())),
            "hit_exports_verified": sum("export_hits_verified" in r for r in records),
            "export_hits_verified": sum(r.get("export_hits_verified", 0) for r in records),
            "folders_without_dta": missing,
            "profiles": [{**json.loads(profile), "files": files} for profile, files in profiles.items()],
            "errors": errors, "files": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Directory containing EMPIRE_acoustic_<number> folders")
    parser.add_argument("--output", type=Path, required=True, help="JSON report location, outside raw data")
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("root must be an existing directory")
    if args.output.resolve().is_relative_to(args.root.resolve()):
        parser.error("write the report outside the raw data directory")
    report = survey(args.root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("files", "profiles")}, indent=2))
    print(f"Distinct hit layouts: {len(report['profiles'])}")
    if not report["dta_files"] or report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
