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

source_input/<institution>/<year>/
    Original PDF statements exactly as received from the institution.
    These files are preserved and ignored by Git.

editable_input/<institution>/<year>/
    Verified unrestricted working copies created from source_input.
    Extraction profiles read PDFs from this folder. These files are ignored by Git.

redacted_input/<institution>/<year>/
    Sanitized copies created from editable_input for development,
    troubleshooting, and regression-test preparation. These files are ignored by Git.

csv_output/<institution>/<year>/
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
Place original statements under:

    source_input/cibc/2024/

Create or verify unrestricted working copies:

    python tests/tools/remove_pdf_security.py

The matching working PDFs are created under:

    editable_input/cibc/2024/

Run extraction:

    python visa_pdf_extractor-v3.py

Select the CIBC profile and PDF folder mode. The application scans year
subfolders under editable_input and mirrors them under:

    csv_output/cibc/2024/

Regression test
---------------
    python -m unittest tests.test_cibc_profile -v

PDF text diagnostic
-------------------
    python tests/tools/diagnose_pdf_text.py editable_input/cibc

Privacy
-------
Source statements, generated output, test statements, and reference CSV
files are ignored by Git. Do not commit unredacted financial information.
