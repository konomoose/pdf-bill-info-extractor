PDF Bill Info Extractor
=======================

Purpose
-------
Extract financial transactions from PDF statements using a saved
institution profile. Each profile stores its input folder, output folder,
parser name, table headings, exclusion phrases, and layout tolerances.

Current profile
---------------
CIBC Credit Card profile version 1 extracts:

- Trans date
- Post date
- Description
- Spend Categories
- Amount($)

The CIBC CreditSmart Spend Report is ignored.

Project folders
---------------
config/profiles/
    Saved institution profiles.

input/<institution>/<year>/
    Source PDF statements. These files are ignored by Git.

output/<institution>/<year>/
    Per-statement transaction CSV files and batch summaries.
    These files are ignored by Git.

src/bill_extractor/
    Profile loading and extraction code.

tests/input/<institution>/
    Optional local redacted statements used for regression tests.

tests/reference_output/<institution>/
    Optional manually verified CSV files used as reference results.

tests/output/<institution>/
    CSV files produced during automated tests.

tests/tools/
    Diagnostic utilities.

CIBC example
------------
Place statements under:

    input/cibc/2024/

Run:

    python visa_pdf_extractor-v3.py

Select the CIBC profile and PDF folder mode. The application scans year
subfolders and mirrors them under:

    output/cibc/2024/

Regression test
---------------
    python -m unittest tests.test_cibc_profile -v

PDF text diagnostic
-------------------
    python tests/tools/diagnose_pdf_text.py input/cibc

Privacy
-------
Source statements, generated output, test statements, and reference CSV
files are ignored by Git. Do not commit unredacted financial information.
