"""Synthetic framing regressions; no personal datasets required."""

import io
from pathlib import Path
import unittest
from unittest.mock import patch

from mistras_dta import DtaFormatError, event_characteristics, iter_messages
from survey_mistras_dta import compare_export


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
    def test_count_time_and_channel_mismatches_are_detected(self):
        header = "ID SSSSSSSS.mmmuuun PARA1 CH\n"
        path = Path("hits.TXT")
        for row in ("", "1 0.0000040 0.0188 1\n", "1 0.0000030 0.0188 2\n", "1 nan 0.0188 1\n"):
            with patch.object(Path, "open", return_value=io.StringIO(header + row)):
                with self.subTest(row=row), self.assertRaises(ValueError):
                    compare_export(path, [(12, 1)])
        with patch.object(Path, "open", return_value=io.StringIO(header + "1 0.0000030 0.0188 1\n")):
            self.assertEqual(compare_export(path, [(12, 1)]), 1)


if __name__ == "__main__":
    unittest.main()
