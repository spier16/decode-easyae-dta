# decode-easyae-dta

Read hits and time-driven data directly from MISTRAS EasyAE / AEwin64 `.DTA` files using
Python 3.10 or newer and the standard library.

The EMPIRE acceptance corpus passes: **180 files, 1,056,916 decoded hits**.
All **20 columns** agree with all **32 manual AEwin64 exports**, covering
170,345 hits and 3,406,900 values, at the exports' printed precision.

Time-data extraction also passes: **180 files, 1,705,249 decoded rows**.
All **eight columns** match all **32 manual time exports**, covering 277,656
rows and 2,221,248 values.

## Install

```shell
python -m pip install decode-easyae-dta
```

From a checkout, including before the first PyPI release, use `python -m pip install .`.
The package has no runtime dependencies. It provides the `decode-easyae-dta`
command and the `decode_easyae_dta` import. Earlier `mistras_dta` imports and
`python mistras_dta.py ...` commands remain compatible.

## Read hits

```python
from decode_easyae_dta import read_hits, iter_hits

hits = read_hits(r"C:\path\to\experiment.DTA")
print(len(hits), hits[0])

# Stream larger recordings; exhaust the iterator to validate the whole file.
for hit in iter_hits(r"C:\path\to\experiment.DTA"):
    amplitude = hit["AMP"]
```

Both `read_hits()` and `read_time_data()` return lists of dictionaries keyed
by the exact AEwin64 CSV headings. For example, use `hits[0]["AMP"]` to get
amplitude in dB or `hits[0]["SSSSSSSS.mmmuuun"]` to get seconds relative to the
recording clock. For optional pandas use, call `pd.DataFrame(read_hits(path))`.

Each hit contains `ID` (message type 1), `CH`, the configured features and
any recorded parametrics (`PARA1` through `PARA8`). Rise time and duration are
in microseconds, frequencies in kHz, and threshold, amplitude and ASL in dB.
RMS and parametrics are in volts, signal strength is in pVs, and absolute
energy is in aJ. `COUN`, `PCNTS`, and `ENER` retain instrument counts. Hits
retain file order.

`iter_hits()` and `iter_time_data()` yield dictionaries one at a time with
the same AEwin64 headings.

## Extract CSV

```powershell
decode-easyae-dta 'C:\path\to\experiment.DTA' --output results/experiment_hits.csv

# Extract the time data instead:
decode-easyae-dta 'C:\path\to\experiment.DTA' --kind time --output results/experiment_time.csv
```

The command validates the input before creating the CSV, preserves numeric
precision, and requires a new output filename. CSV headings and column order match
the AEwin64 exports, including `PARA1`, `SIG STRNGTH`, and `R-FRQ`.

Each CSV starts with four metadata rows and a blank line:

```csv
Source File,C:\path\to\experiment.DTA
Product,EasyAE (r) Location Version
Version,V1.00
Test Start,Mon Oct 26 11:33:41 2026

ID,SSSSSSSS.mmmuuun,PARA1,CH,...
```

The product/version come from binary message ID 41; the test-start text comes
from ID 99. The source path is the current input file's absolute path. The stored
test time has no timezone, and missing metadata is shown as `Not recorded`.
Use `--no-metadata` for a CSV beginning directly with the data column headings.
For pandas, read the default CSV using `pd.read_csv(path, skiprows=5)`.

Metadata is also available separately in Python:

```python
from decode_easyae_dta import read_metadata

metadata = read_metadata(r"C:\path\to\experiment.DTA")
print(metadata.source_file, metadata.test_start)
```

## Read time data

```python
from decode_easyae_dta import read_time_data, iter_time_data, write_time_csv, read_metadata

path = r"C:\path\to\experiment.DTA"
rows = read_time_data(path)
print(len(rows), rows[0])

with open("experiment_time.csv", "x", encoding="utf-8", newline="") as stream:
    write_time_csv(rows, stream, metadata=read_metadata(path))
```

The time CSV uses `ID,SSSSSSSS.mmmuuun,PARA1,CH,RMS,THR,ABS-ENERGY,ASL`
for this corpus. AEwin64's text exports place the channel values in a separate
bracketed block below each timestamp. The CSV flattens that block into one row
per timestamp and channel, repeating timestamp and parametric values across
channels. The corpus uses one channel. Numeric values retain full decoded
precision; timestamps use the recording clock, as in the hit CSV.

`read_time_data()` uses the same keys as the CSV. Parametric-only rows have
no `CH` key. `ID` is 2 for time-driven records and 3 for forced samples.
`write_time_csv()` and `write_hits_csv()` accept rows from either the list-based
readers or the streaming iterators.

## Validate

```powershell
python -m unittest -v
$env:EMPIRE_AE_DATA = 'C:\path\to\EMPIRE_acoustic_tests'
python validate_mistras_dta.py "$env:EMPIRE_AE_DATA" --output results/empire_hit_validation.json
python validate_mistras_dta.py "$env:EMPIRE_AE_DATA" --kind time --output results/empire_time_validation.json

# Or compare a single pair:
python validate_mistras_dta.py 'C:\path\to\experiment.DTA' --export 'C:\path\to\experiment_hits.TXT'
python validate_mistras_dta.py 'C:\path\to\experiment.DTA' --kind time --export 'C:\path\to\experiment_time.TXT'
```

The corpus command visits numbered `EMPIRE_acoustic_<number>` folders, excluding
`misc`. It reads every DTA, compares every column in every corresponding
`<DTA stem>_hits.txt` export (or `_time.txt` with `--kind time`), and writes a
report outside the raw-data directory.
The acquisition corpus and generated reports are kept locally and are not
included in the public repository or package.
Counts and integer features must agree exactly; other values must agree within
half the last printed decimal place. Failures produce a nonzero exit status.
The comparator checks schema equality and treats `SIG STRNGTH` as one column.

The earlier framing-only survey remains available:

```powershell
python survey_mistras_dta.py "$env:EMPIRE_AE_DATA" --output results/empire_dta_survey.json
```

## Supported scope

This reader supports the **EasyAE V1.00 profile and calibration/scaling metadata
verified in the EMPIRE corpus**. It reads the configured feature order and checks
the product, hardware version, preamp/gain, channel calibration and parametric
scaling records. Unsupported characteristics or calibration settings raise
`DtaFormatError`, including byte offsets. General custom multiplier/offset
decoding remains unimplemented; the verified unity-scaling record is recognized
as a whole. Agreement at export precision does not establish extra digits of
parametric calibration accuracy.

Start markers are optional; the extra control byte is safely framed. Unknown
message IDs are skipped using declared boundaries. Waveforms,
continued-file setup and other instrument profiles are outside this milestone.
Time-data characteristics are restricted to the verified THR, RMS, ABS-ENERGY
and ASL encodings. Custom parametric scaling and cycle counters are unsupported.
Two-channel decoding also matches a separate real dual-sensor acquisition's
hit and time exports.
Multiple analog parametrics, schema updates, and ID 3 have synthetic coverage
only. The real time exports exercise PARA1.

Raw binaries, TXT exports and the vendor manual stay external to this repository.
The project uses the MIT license in [LICENSE](https://github.com/spier16/decode-easyae-dta/blob/main/LICENSE).
For development, see [CONTRIBUTING.md](https://github.com/spier16/decode-easyae-dta/blob/main/CONTRIBUTING.md). The separate
[decode-wfs](https://github.com/spier16/decode-wfs) project decodes waveform streams.
