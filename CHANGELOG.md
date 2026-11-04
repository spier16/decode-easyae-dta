# Changelog

## 0.1.0

- Decode configured hit features and analog parametrics from the validated
  EasyAE V1.00 DTA profile, with streaming and list-based Python APIs.
- Decode time-driven and forced-sample records into timestamp/channel rows.
- Export CSV with AEwin64 column names, source filename, product/version and
  the recorded test-start date.
- Provide the `decode-easyae-dta` command and retain `mistras_dta` compatibility.
- Validate against 32 paired hit exports, 32 time exports, and a separate real
  two-channel acquisition. Reject unsupported calibration and schema settings.
