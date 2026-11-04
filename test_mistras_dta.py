"""Portable framing, decoding and full-column comparison regressions."""

import io
import csv
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

from decode_easyae_dta import DtaFormatError, event_characteristics, iter_messages
from decode_easyae_dta import read_metadata, write_hits_csv, read_time_data, write_time_csv
from survey_mistras_dta import compare_export
from validate_mistras_dta import compare_hits, compare_time_data, read_time_export


def frame(payload):
    return len(payload).to_bytes(2, "little") + payload


class FramingTests(unittest.TestCase):
    def test_start_marker_extra_byte_does_not_hide_next_hit(self):
        start = b"\x80" + bytes(6) + b"\x01"
        hit = b"\x01" + (12).to_bytes(6, "little") + b"\x01" + bytes(40)
        messages = list(iter_messages(io.BytesIO(frame(start) + frame(hit))))
        self.assertEqual([m.id for m in messages], [128, 1])
        self.assertEqual(messages[1].offset, 10)
        self.assertEqual(messages[1].payload, hit)

    def test_unknown_message_is_preserved(self):
        payloads = [b"\xfa\x01\x02", b"\x29\x00\xbc\x02text", b"\x0f"]
        messages = list(iter_messages(io.BytesIO(b"".join(map(frame, payloads)))))
        self.assertEqual([m.payload for m in messages], payloads)

    def test_truncation_and_empty_messages_are_errors(self):
        for data in (b"\x01", b"\x03\x00\x01", b"\x00\x00"):
            with self.subTest(data=data), self.assertRaises(DtaFormatError):
                list(iter_messages(io.BytesIO(data)))

    def test_setup_uses_subrecord_boundaries_and_keeps_new_ids(self):
        setup = b"\x2a\x00\x67\x00" + frame(b"\x64") + frame(bytes([5, 3, 1, 27, 28]))
        message = next(iter_messages(io.BytesIO(frame(setup))))
        self.assertEqual(event_characteristics(message), [(1, 27, 28)])

    def test_truncated_characteristic_list_is_an_error(self):
        setup = b"\x2a\x00\x67\x00" + frame(bytes([5, 3, 1]))
        message = next(iter_messages(io.BytesIO(frame(setup))))
        with self.assertRaises(DtaFormatError):
            event_characteristics(message)


class ExportComparisonTests(unittest.TestCase):
    def test_all_columns_and_printed_precision(self):
        header = "ID SSSSSSSS.mmmuuun CH SIG STRNGTH ABS-ENERGY AMP PARA1\n"
        row = "1 0.0000030 1 53.317E+03 50.688E+00 25.9 0.0188\n"
        hit = dict(id=1, time_s=0.000003, channel=1, signal_strength_pvs=53317.05,
                   absolute_energy_aj=50.6883, amplitude_db=25.9, parametric_1=0.01877)
        path = Path("hits.TXT")
        with patch.object(Path, "open", side_effect=lambda **kwargs: io.StringIO(header + row)):
            self.assertEqual(compare_hits(path, [hit])["values"], 7)
            for key in hit:
                changed = dict(hit)
                changed[key] += 1
                with self.subTest(key=key), self.assertRaises(ValueError):
                    compare_hits(path, [changed])
            for changed in ([], [{**hit, "unexpected": 1}], [{**hit, "amplitude_db": float("nan")}],
                            [{**hit, "absolute_energy_aj": hit["absolute_energy_aj"] * 1000}]):
                with self.assertRaises(ValueError):
                    compare_hits(path, changed)

    def test_count_time_and_channel_mismatches_are_detected(self):
        header = "ID SSSSSSSS.mmmuuun PARA1 CH\n"
        path = Path("hits.TXT")
        for row in ("", "1 0.0000040 0.0188 1\n", "1 0.0000030 0.0188 2\n", "1 nan 0.0188 1\n"):
            with patch.object(Path, "open", return_value=io.StringIO(header + row)):
                with self.subTest(row=row), self.assertRaises(ValueError):
                    compare_export(path, [(12, 1)])
        with patch.object(Path, "open", return_value=io.StringIO(header + "1 0.0000030 0.0188 1\n")):
            self.assertEqual(compare_export(path, [(12, 1)]), 1)


class MetadataTests(unittest.TestCase):
    def metadata(self, data):
        with patch.object(Path, "open", return_value=io.BytesIO(data)):
            return read_metadata(Path("test, recording.DTA"))

    def test_recorded_date_and_product(self):
        data = setup() + frame(b"\x63Mon Oct 26 11:33:41 2026\n\x00")
        metadata = self.metadata(data)
        self.assertEqual(metadata.source_file, str(Path("test, recording.DTA").resolve()))
        self.assertEqual(metadata.product, "EasyAE (r) Location Version")
        self.assertEqual(metadata.version, "V1.00")
        self.assertEqual(metadata.test_start, "Mon Oct 26 11:33:41 2026")
        stream = io.StringIO(newline="")
        write_hits_csv([dict(id=1, time_s=0.0, channel=1)], stream, metadata=metadata)
        rows = list(csv.reader(io.StringIO(stream.getvalue())))
        self.assertEqual(rows[:5], [["Source File", metadata.source_file],
                                   ["Product", metadata.product], ["Version", "V1.00"],
                                   ["Test Start", metadata.test_start], []])
        self.assertEqual(rows[5:], [["ID", "SSSSSSSS.mmmuuun", "CH"], ["1", "0.0", "1"]])

    def test_missing_date_is_not_invented(self):
        metadata = self.metadata(setup())
        self.assertIsNone(metadata.test_start)
        stream = io.StringIO(newline="")
        write_hits_csv([], stream, metadata=metadata)
        self.assertIn("Test Start,Not recorded", stream.getvalue())

    def test_invalid_or_conflicting_metadata_fails(self):
        for data in (frame(b"\x63\xff\x00"), frame(b"\x63\n\x00"), frame(b"\x29\x00"),
                     frame(b"\x63date1\n\x00") + frame(b"\x63date2\n\x00"),
                     frame(b"\x63date1\ndate2\x00")):
            with self.subTest(data=data), self.assertRaises(DtaFormatError):
                self.metadata(data)


CHARACTERISTICS = (1, 2, 3, 4, 5, 10, 13, 17, 18, 19, 20, 21, 23, 24, 27, 28)
UNITY_SCALING = b"\x6b\x10\x27\x08\x00" + (b"\x00" + struct.pack("<iiH", 0, 1000, 0)) * 8


def setup(characteristics=CHARACTERISTICS, parametrics=1):
    return (frame(b")\x00\xbc\x02EasyAE (r) Location Version \n\rVersion V1.00\n\r\n\r\x1a\x00")
            + frame(UNITY_SCALING)
            + frame(b"*\x00\x67\x00" + frame(bytes([5, len(characteristics), *characteristics, parametrics]))
                    + frame(b"\x14\x01\x1a") + frame(b"\x17\x01\x00\x14")
                    + frame(b"\xb0\x2a\x65\x00\x01" + struct.pack("<dI", 1.0, 1000000))))


def synthetic_hit():
    features = struct.pack("<HHHHIBHHHHIfHHHH", 15906, 347, 348, 8, 16001,
                           10, 22, 0, 10, 21, 17481, 54445, 160, 2, 259, 1)
    return frame(b"\x01" + (12).to_bytes(6, "little") + b"\x01" + features + b"\x01\x7b\x00")


class HitDecodingTests(unittest.TestCase):
    def decode(self, data):
        from decode_easyae_dta import read_hits
        with patch.object(Path, "open", return_value=io.BytesIO(data)):
            return read_hits(Path("test.DTA"))

    def test_feature_values_with_and_without_start_marker(self):
        expected = dict(id=1, time_s=0.000003, channel=1, rise_us=15906, counts_to_peak=347,
                        counts=348, energy=8, duration_us=16001, threshold_db=10,
                        average_frequency_khz=22, rms_v=0.0, reverberation_frequency_khz=10,
                        initiation_frequency_khz=21, signal_strength_pvs=53317.05078125,
                        absolute_energy_aj=50.68829345703125, centroid_frequency_khz=160,
                        peak_frequency_khz=2, amplitude_db=25.9, asl_db=0.1,
                        parametric_1=123 * 10 / 65536)
        for marker in (b"", frame(b"\x80" + bytes(6) + b"\x01")):
            with self.subTest(marker=marker):
                self.assertEqual(self.decode(setup() + marker + synthetic_hit()), [expected])

    def test_definition_order_and_multiple_unsigned_parametrics(self):
        hit = frame(b"\x01" + bytes(6) + b"\x01" + struct.pack("<HH", 450, 42)
                    + b"\x02\xff\xff\x01\x00\x80")
        result = self.decode(setup((27, 1), 2) + hit)[0]
        self.assertEqual(result["amplitude_db"], 45)
        self.assertEqual(result["rise_us"], 42)
        self.assertEqual(result["parametric_1"], 5)
        self.assertEqual(result["parametric_2"], 65535 * 10 / 65536)

    def test_single_precision_energy_multiplication(self):
        hit = frame(b"\x01" + bytes(6) + b"\x01" + struct.pack("<f", 192384))
        value = self.decode(setup((21,), 0) + hit)[0]["absolute_energy_aj"]
        self.assertEqual(value, 179.1094970703125)

    def test_single_precision_signal_strength(self):
        hit = frame(b"\x01" + bytes(6) + b"\x01" + struct.pack("<I", 17377541))
        value = self.decode(setup((20,), 0) + hit)[0]["signal_strength_pvs"]
        self.assertLessEqual(abs(value - 53001000), 500)
        self.assertGreater(17377541 * 3.05 - 53001000, 500)

    def test_comparison_catches_decoder_scaling_regression(self):
        import decode_easyae_dta
        path = Path("hits.TXT")
        header = "ID SSSSSSSS.mmmuuun CH SIG STRNGTH\n1 0.0000000 1 53.317E+03\n"
        data = setup((20,), 0) + frame(b"\x01" + bytes(6) + b"\x01" + struct.pack("<I", 17481))
        correct = self.decode(data)
        with patch.dict(decode_easyae_dta._FEATURES, {20: ("signal_strength_pvs", "I", 3.0517578125)}):
            wrong = self.decode(data)
        with patch.object(Path, "open", side_effect=lambda **kwargs: io.StringIO(header)):
            compare_hits(path, correct)
            with self.assertRaisesRegex(ValueError, "SIG_STRNGTH mismatch"):
                compare_hits(path, wrong)

    def test_streaming_and_setup_changes(self):
        data = setup((1,), 0) + frame(b"\x01" + bytes(6) + b"\x01\x2a\x00")
        data += frame(b"\xfa\x01\x02") + frame(bytes([5, 1, 27, 0]))
        data += frame(b"\x01" + bytes(6) + b"\x01\xc2\x01")
        hits = self.decode(data)
        self.assertEqual(hits[0]["rise_us"], 42)
        self.assertEqual(hits[1]["amplitude_db"], 45)

    def test_bad_parametric_records(self):
        prefix = b"\x01" + bytes(6) + b"\x01"
        for body in (b"\x00\x00\x00", b"\x09\x00\x00", b"\x01\x00\x00\x01\x00\x00",
                     b"\x01\x00", b"\x01\x00\x00\x02\x00\x00\x03\x00\x00"):
            with self.subTest(body=body), self.assertRaises(DtaFormatError):
                self.decode(setup((), 2) + frame(prefix + body))

    def test_unsupported_and_malformed_inputs_are_explicit(self):
        cases = [b"", synthetic_hit(), setup((99,)), setup() + synthetic_hit()[:-1],
                 setup() + frame(b"\x01" + bytes(47) + b"\x00"),
                 setup((21,), 0) + frame(b"\x01" + bytes(6) + b"\x01" + struct.pack("<f", float("nan"))),
                 setup().replace(UNITY_SCALING, UNITY_SCALING[:-1] + b"\x01"),
                 setup().replace(b"\x17\x01\x00\x14", b"\x17\x01\x01\x14"),
                 setup().replace(b"\x14\x01\x1a", b"\x14\x01\x28"),
                 setup().replace(struct.pack("<dI", 1.0, 1000000), struct.pack("<dI", 2.0, 1000000)),
                 setup((1, 1)), setup() + frame(b"\x08"),
                 setup().replace(b"Version V1.00", b"Version V9.99"),
                 setup().replace(b"*\x00\x67\x00", b"*\x00\x68\x00"),
                 setup() + frame(b"\x01" + bytes(6))]
        for data in cases:
            with self.subTest(data=data[:40]), self.assertRaises(DtaFormatError):
                self.decode(data)

    def test_valid_empty_file(self):
        self.assertEqual(self.decode(setup()), [])


def time_setup(characteristics=(10, 17, 21, 28), parametrics=(1,)):
    definition = bytes([6, len(characteristics), *characteristics, len(parametrics), *parametrics])
    return setup() + frame(b"*\x00\x67\x00" + frame(definition))


def synthetic_time(ident=2, channels=b"\x01" + struct.pack("<BHfH", 10, 1250, 192384, 158),
                   parametrics=b"\x01\x7e\x00"):
    return frame(bytes([ident]) + (40386).to_bytes(6, "little") + parametrics + channels)


class TimeDecodingTests(unittest.TestCase):
    def decode(self, data):
        with patch.object(Path, "open", return_value=io.BytesIO(data)):
            return read_time_data(Path("test.DTA"))

    def test_values_and_csv_use_time_definition(self):
        data = time_setup() + synthetic_hit() + synthetic_time()
        row = self.decode(data)[0]
        self.assertEqual(row, dict(id=2, time_s=0.0100965, parametric_1=126 * 10 / 65536,
                                  channel=1, threshold_db=10, rms_v=0.25,
                                  absolute_energy_aj=179.1094970703125, asl_db=15.8))
        stream = io.StringIO(newline="")
        write_time_csv(iter([row]), stream)
        table = list(csv.reader(io.StringIO(stream.getvalue())))
        self.assertEqual(table[0], ["ID", "SSSSSSSS.mmmuuun", "PARA1", "CH", "RMS", "THR", "ABS-ENERGY", "ASL"])
        self.assertEqual([float(v) for v in table[1]],
                         [2, 0.0100965, 126 * 10 / 65536, 1, 0.25, 10, 179.1094970703125, 15.8])

    def test_multiple_channels_parametrics_and_sample_id(self):
        calibration = (frame(b"\x14\x02\x1a") + frame(b"\x17\x02\x00\x14")
                       + frame(b"\xb0\x2a\x65\x00\x02" + struct.pack("<dI", 1.0, 1000000)))
        data = time_setup((28, 17), (2, 1)) + calibration
        data += synthetic_time(3, b"\x02" + struct.pack("<HH", 200, 5000)
                               + b"\x01" + struct.pack("<HH", 158, 1250),
                               b"\x02\xff\xff\x01\x00\x80")
        rows = self.decode(data)
        self.assertEqual([row["channel"] for row in rows], [2, 1])
        self.assertEqual([row["asl_db"] for row in rows], [20, 15.8])
        self.assertEqual([row["rms_v"] for row in rows], [1, 0.25])
        for row in rows:
            self.assertEqual(row["id"], 3)
            self.assertEqual(row["parametric_1"], 5)
            self.assertEqual(row["parametric_2"], 65535 * 10 / 65536)

    def test_parametric_only_and_definition_updates(self):
        data = time_setup((), (1,)) + synthetic_time(channels=b"")
        data += frame(bytes([6, 1, 28, 0]))
        data += frame(bytes([5, 1, 1, 0]))  # Updating hits must not change time data.
        data += synthetic_time(channels=b"\x01\x9e\x00", parametrics=b"")
        rows = self.decode(data)
        self.assertEqual(set(rows[0]), {"id", "time_s", "parametric_1"})
        self.assertEqual(rows[1], dict(id=2, time_s=0.0100965, channel=1, asl_db=15.8))
        self.assertEqual(self.decode(time_setup()), [])

    def test_unsupported_and_malformed_time_data(self):
        cases = [b"", setup() + synthetic_time(), time_setup() + frame(b"\x02"),
                 time_setup((99,)), time_setup((28, 28)), time_setup((), (9,)),
                 time_setup((), (1, 1)), setup() + frame(b"\x06\x04\x0a"),
                 setup() + frame(b"\x06\x00\x02\x01"),
                 time_setup() + synthetic_time(channels=b""),
                 time_setup() + synthetic_time(channels=b"\x01\x0a"),
                 time_setup() + synthetic_time(parametrics=b"\x02\x7e\x00"),
                 time_setup() + synthetic_time(parametrics=b""),
                 time_setup() + synthetic_time(channels=(b"\x01" + struct.pack("<BHfH", 10, 0, 0, 0)) * 2),
                 time_setup() + synthetic_time(channels=b"\x02" + struct.pack("<BHfH", 10, 0, 0, 0)),
                 time_setup() + synthetic_time(channels=b"\x01" + struct.pack("<BHfH", 10, 0, float("nan"), 0)),
                 time_setup(()) + synthetic_time(), time_setup() + frame(b"\x08")]
        for data in cases:
            with self.subTest(data=data[-30:]), self.assertRaises(DtaFormatError):
                self.decode(data)


class TimeExportComparisonTests(unittest.TestCase):
    header = "ID SSSSSSSS.mmmuuun PARA1\n"
    block = ("2 0.0100965 0.0192\n CH:[ RMS THR ABS-ENERGY ASL]\n"
             "1:[ 0.2500 10 179.109E+00 15.8]\n2:[ 0.5000 11 0.000E+00 20.0]\n")

    def compare(self, text, rows):
        with patch.object(Path, "open", side_effect=lambda **kwargs: io.StringIO(text)):
            return compare_time_data(Path("time.TXT"), rows)

    def test_all_columns_multiple_channels_and_corruption(self):
        common = dict(id=2, time_s=0.0100965, parametric_1=126 * 10 / 65536)
        rows = [{**common, "channel": 1, "rms_v": 0.25, "threshold_db": 10,
                 "absolute_energy_aj": 179.1094970703125, "asl_db": 15.8},
                {**common, "channel": 2, "rms_v": 0.5, "threshold_db": 11,
                 "absolute_energy_aj": 0, "asl_db": 20}]
        text = self.header + self.block
        self.assertEqual(self.compare(text, rows)["values"], 16)
        for key in rows[0]:
            changed = [{**rows[0], key: rows[0][key] + 1}, rows[1]]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.compare(text, changed)
        for changed in (rows[::-1], rows[:1], [{**rows[0], "rms_v": float("nan")}, rows[1]]):
            with self.assertRaises(ValueError):
                self.compare(text, changed)
        self.assertEqual(self.compare(text + self.block, rows * 2)["rows"], 4)

    def test_invalid_blocks(self):
        for block in ("CH:[ RMS]\n", "2 0.0 0.0\nCH:[ RMS]\n", "2 0.0\n",
                      "2 0.0 0.0\n1:[ 0.0]\n", "2 0.0 0.0\nCH:[ RMS RMS]\n",
                      "2 0.0 0.0\nCH:[ RMS]\n1:[ 0.0 0.0]\n",
                      "2 0.0 0.0\nCH:[ RMS]\n1:[ 0.0]\n1:[ 0.0]\n",
                      "2 0.0 0.0\nCH:[ RMS] CH:[ RMS]\n1:[ 0.0]\n",
                      "2 0.0 0.0\nCH:[ RMS] garbage\n",
                      "2 0.0 0.0\nCH:[ RMS]\nCH:[ RMS]\n", "1 0.0 0.0\n"):
            with self.subTest(block=block), self.assertRaises(ValueError):
                with patch.object(Path, "open", return_value=io.StringIO(self.header + block)):
                    read_time_export(Path("time.TXT"))

    def test_side_by_side_channels_match_stacked_blocks(self):
        parallel = ("2 0.0100965 0.0192\n"
                    "CH:[ RMS THR ABS-ENERGY ASL] CH:[ RMS THR ABS-ENERGY ASL]\n"
                    "1:[ 0.2500 10 179.109E+00 15.8] 2:[ 0.5000 11 0.000E+00 20.0]\n")
        parsed = []
        for block in (self.block, parallel):
            with patch.object(Path, "open", return_value=io.StringIO(self.header + block)):
                parsed.append(read_time_export(Path("time.TXT")))
        self.assertEqual(parsed[0], parsed[1])
        with patch.object(Path, "open", return_value=io.StringIO(self.header + parallel.replace("2:[", "1:["))):
            with self.assertRaisesRegex(ValueError, "Duplicate channel"):
                read_time_export(Path("time.TXT"))


if __name__ == "__main__":
    unittest.main()
