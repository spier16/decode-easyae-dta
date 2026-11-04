# Contributing

Use Python 3.10 or newer. The decoder and tests need only the standard library.

```shell
python -m pip install -e ".[dev]"
python -m unittest -v
```

Keep the public APIs dependency-free. Preserve the original recording clock and
the physical units identified by the field names and described in the README.
Unsupported layouts must fail explicitly. Include a portable synthetic fixture
for decoder changes and compare against independent AEwin64 exports when available.
Do not commit raw DTA/WFS files, vendor manuals, credentials or personal data paths.
The optional external-data validation commands are in the README. Input files
remain read-only; generated output belongs under the ignored `results/` directory.

To check the distribution:

```shell
python -m build
python -m twine check dist/*
```

Internal notes, diagnostic notebooks and generated reports are local working
material excluded by `.gitignore`. Keep public usage and support information
in the README.
