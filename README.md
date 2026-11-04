# EasyAE DTA decoder

Native Python reader under development for MISTRAS EasyAE / AEWin64 `.DTA`
files. This folder is self-contained: copy its contents into a new repository
and continue with [HANDOFF.md](HANDOFF.md).

## Current functionality

- Read complete length-delimited messages, preserving unknown IDs and trailing
  bytes; report malformed boundaries with byte offsets.
- Extract ordered event characteristic IDs from hardware setup records.
- Survey numbered experiment folders and compare hit counts, relative times,
  and channels with existing AEWin64 hit exports.

Full hit feature decoding, parametric voltage scaling, and `read_hits(path)`
are the next milestone. Waveforms and time-driven values are not decoded yet.

## Run

Python 3.9 or newer; the utility and tests use only the standard library.
Run these commands from this folder, or from the new repository root after
copying the contents there:

```powershell
python -m unittest -v
$env:EMPIRE_AE_DATA = 'C:\path\to\EMPIRE_acoustic_tests'
python survey_mistras_dta.py "$env:EMPIRE_AE_DATA" --output results/empire_dta_survey.json
```

The survey expects `EMPIRE_acoustic_<number>` subfolders and optional matching
`<DTA stem>_hits.TXT` files beside the DTA files. It reads the source data without
modifying it and writes the JSON report outside the raw data directory. A
malformed file, export mismatch, or empty corpus gives a nonzero exit status.

## Included material

- [HANDOFF.md](HANDOFF.md): data location, known failure, next task, and acceptance criteria.
- [docs/empire-dta-survey.md](docs/empire-dta-survey.md): verified corpus baseline.
- [docs/mistras-dta-format-research.md](docs/mistras-dta-format-research.md): manual and source references.
- [reports/empire_dta_survey.json](reports/empire_dta_survey.json): complete initial survey using relative paths.
- [diagnostics/test_mistras_dta.ipynb](diagnostics/test_mistras_dta.ipynb): original `MistrasDTA` reproduction notebook.

The diagnostic notebook requires the third-party `MistrasDTA` package and uses
the original local data path. It is historical context, not required to run
this utility. Raw binaries, TXT exports, and the vendor manual are external
inputs. The project carries the original AELab MIT license in [LICENSE](LICENSE).
