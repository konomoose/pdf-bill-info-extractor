import os
import pandas as pd
import pdfplumber
import camelot
import re
from datetime import datetime
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class VisaPDFProcessor:
    # --- Add at top of class (constants) ---
    TARGET_HEADERS = ["Trans date", "Post date", "Description", "Spend Categories", "Amount($)"]
    TARGET_HEADERS_LOWER = [h.lower() for h in TARGET_HEADERS]

    # --- Add: crop helpers to exclude headers/footers before extraction ---
    def _crop_content_area(self, page, top_margin=80, bottom_margin=70, left_margin=10, right_margin=10):
        """Return a pdfplumber cropped page that excludes header & footer bands."""
        W, H = page.width, page.height
        bbox = (left_margin, top_margin, W - right_margin, H - bottom_margin)
        return page.within_bbox(bbox)

    # --- Add: normalize & repair headers (join split tokens, rename variants) ---
    def _normalize_headers(self, cols):
        """
        Given a list of raw column names (possibly split), join neighbors to match
        the 5 canonical headers. Also strip spaces and fix case/variants.
        """
        raw = [("" if c is None else str(c)).strip() for c in cols]

        # Join obvious broken tokens (e.g., "Spen","d Categories" -> "Spend Categories")
        joined = []
        i = 0
        while i < len(raw):
            tok = raw[i]
            if i + 1 < len(raw):
                pair = (tok + " " + raw[i+1]).strip()
                if pair.lower() in ["spend categories", "annual interest rate"]:
                    joined.append(pair)
                    i += 2
                    continue
            joined.append(tok)
            i += 1

        # Common normalizations / aliases
        renames = {
            "trans date": "Trans date",
            "transaction date": "Trans date",
            "post date": "Post date",
            "description": "Description",
            "spend categories": "Spend Categories",
            "amount($)": "Amount($)",
            "amount ($)": "Amount($)",
            "amount": "Amount($)",  # sometimes PDF drops the ($)
        }

        norm = []
        for c in joined:
            lc = c.lower()
            norm.append(renames.get(lc, c))

        # Keep only those that can map to targets or look like them
        # (avoid extra garbage columns)
        filtered = []
        for c in norm:
            cl = c.lower()
            # crude fuzzy: accept if exact target or partial "amount", "date", etc.
            if cl in [h.lower() for h in TARGET_HEADERS] or \
               cl in ["amount", "amount ($)", "amount($)", "spend categories", "trans date", "post date", "description"]:
                filtered.append(renames.get(cl, c))
            else:
                # ignore odd fragments like "Column_4", "An", "nual inter", etc.
                pass

        # Deduplicate while preserving order
        seen = set()
        dedup = []
        for c in filtered:
            if c not in seen:
                dedup.append(c)
                seen.add(c)

        # If we still don't match exactly, try to coerce order to the canonical one
        final = []
        for h in TARGET_HEADERS:
            # pick the first column that case-insensitively matches h
            match = next((c for c in dedup if c.lower() == h.lower()), None)
            if match is not None:
                final.append(match)
            else:
                # allow missing for now; we'll drop later if not found
                final.append(h)

        return final

    def _looks_like_target_header_row(self, row_cells):
        txt = " ".join([(str(x) if x is not None else "") for x in row_cells]).lower()
        return all(h in txt for h in ["date", "post", "description", "amount"]) and "spend" in txt

    # --- Add: clean a single raw table to our canonical 5 columns ---
    def _clean_to_target(self, df):
        if df is None or df.empty:
            return None

        # Drop all-empty rows/cols
        df = df.copy()
        df = df.dropna(how="all").dropna(axis=1, how="all")
        df = df.applymap(lambda x: str(x).strip() if pd.notna(x) else x)

        # If first row looks like headers, promote it
        if df.shape[0] > 0 and self._looks_like_target_header_row(df.iloc[0].tolist()):
            df.columns = self._normalize_headers(df.iloc[0].tolist())
            df = df.iloc[1:].reset_index(drop=True)
        else:
            # Otherwise, try using current header row as-is but normalize
            df.columns = self._normalize_headers(df.columns.tolist())

        # Keep only canonical columns that actually exist
        keep = [c for c in df.columns if c.lower() in TARGET_HEADERS_LOWER]
        df = df[keep]

        # Finally, reorder to canonical order and drop columns we couldn't resolve
        cols_present = set(c.lower() for c in df.columns)
        ordered = [h for h in TARGET_HEADERS if h.lower() in cols_present]
        df = df.rename(columns={c: next(h for h in TARGET_HEADERS if h.lower() == c.lower()) for c in df.columns})
        df = df[ordered].reset_index(drop=True)

        # Drop any accidental duplicate header rows that re-appeared after a page break
        if not df.empty:
            mask_header_dupes = df.apply(lambda r: self._looks_like_target_header_row(r.tolist()), axis=1)
            df = df[~mask_header_dupes].reset_index(drop=True)

        # Remove fully-empty rows after trimming
        df = df.replace(r"^\s*$", pd.NA, regex=True).dropna(how="all").reset_index(drop=True)

        return df if not df.empty else None

    # --- Add: stitch consecutive tables that share the same 5-col header ---
    def _stitch_target_tables(self, tables_in_order):
        """
        Given a list of cleaned DataFrames (already normalized), stitch
        consecutive ones that share the target header set.
        """
        stitched = []
        buffer_df = None

        def is_target_df(x):
            return x is not None and list(x.columns) and \
                   all(h in x.columns for h in TARGET_HEADERS[:3]) and \
                   any(h in x.columns for h in ["Spend Categories"]) and \
                   any(h in x.columns for h in ["Amount($)"])

        for t in tables_in_order:
            if not is_target_df(t):
                # Flush buffer if present
                if buffer_df is not None and not buffer_df.empty:
                    stitched.append(buffer_df.reset_index(drop=True))
                    buffer_df = None
                continue

            if buffer_df is None:
                buffer_df = t.copy()
            else:
                # Same schema? Align columns and append.
                shared = [c for c in TARGET_HEADERS if c in buffer_df.columns and c in t.columns]
                if len(shared) >= 3:
                    to_add = t[shared]
                    buffer_df = pd.concat([buffer_df[shared], to_add], ignore_index=True)
                else:
                    stitched.append(buffer_df.reset_index(drop=True))
                    buffer_df = t.copy()

        if buffer_df is not None and not buffer_df.empty:
            stitched.append(buffer_df.reset_index(drop=True))

        # Final dedupe of header rows (in case any slipped through)
        cleaned = []
        for df in stitched:
            mask_header_dupes = df.apply(lambda r: self._looks_like_target_header_row(r.tolist()), axis=1)
            cleaned.append(df[~mask_header_dupes].reset_index(drop=True))
        return cleaned

    # --- REPLACE: extract with pdfplumber using crops & tuned settings ---
    def extract_tables_with_pdfplumber(self, pdf_path):
        """
        Extract tables using pdfplumber with header/footer cropping and
        slightly stricter table_settings to prevent header/footer noise.
        """
        tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    crop = self._crop_content_area(page)  # remove header/footer bands
                    # tuned table settings: rely on text positions; snap small gaps
                    settings = {
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "intersection_tolerance": 5,
                        "snap_tolerance": 3,
                        "join_tolerance": 3,
                        "edge_min_length": 3,
                        "min_words_vertical": 1,
                        "min_words_horizontal": 1,
                        "keep_blank_chars": False,
                    }
                    page_tables = crop.extract_tables(table_settings=settings)
                    for ti, tbl in enumerate(page_tables, start=1):
                        if not tbl or len(tbl) == 0:
                            continue
                        df = pd.DataFrame(tbl[1:], columns=tbl[0])
                        if not df.empty:
                            tables.append({"page": page_num, "table_num": ti, "data": df})
        except Exception as e:
            logger.error(f"Error extracting tables with pdfplumber from {pdf_path}: {str(e)}")
        return tables

    # --- OPTIONAL: narrow Camelot to a content band as well (if you use Camelot) ---
    def extract_tables_with_camelot(self, pdf_path):
        """
        Try Camelot after pdfplumber; favor 'stream' with edge_tol.
        (Note: without fixed coordinates, Camelot can't crop per-page like pdfplumber,
        but edge_tol/split_text help reduce header/footer noise.)
        """
        tables = []
        try:
            for flavor in ["stream", "lattice"]:
                try:
                    camelot_tables = camelot.read_pdf(
                        pdf_path,
                        flavor=flavor,
                        pages="all",
                        strip_text="\n",
                        edge_tol=200 if flavor == "stream" else 50,
                        split_text=True
                    )
                    for i, t in enumerate(camelot_tables):
                        if not t.df.empty:
                            tables.append({"page": t.page, "table_num": i + 1, "data": t.df})
                    if tables:
                        break
                except Exception:
                    continue
        except Exception as e:
            logger.error(f"Error extracting tables with camelot from {pdf_path}: {str(e)}")
        return tables

    # --- REPLACE: process_pdf to use cleaning + stitching to one final table ---
    def process_pdf(self, pdf_path):
        """Process a single PDF file and return cleaned/stiched financial tables."""
        filename = os.path.basename(pdf_path)
        logger.info(f"Processing {filename}")

        # 1) Extract (cropped) with pdfplumber first; then Camelot as backup
        raw_tables = self.extract_tables_with_pdfplumber(pdf_path)
        raw_tables += self.extract_tables_with_camelot(pdf_path)

        if not raw_tables:
            return []

        # 2) Clean each to canonical (if possible)
        cleaned = []
        for t in sorted(raw_tables, key=lambda x: (x["page"], x["table_num"])):
            ct = self._clean_to_target(t["data"])
            if ct is not None and not ct.empty:
                cleaned.append({"page": t["page"], "table_num": t["table_num"], "data": ct})

        if not cleaned:
            return []

        # 3) Stitch consecutive tables with the same 5-col schema (multi-page continuation)
        ordered_dfs = [t["data"] for t in cleaned]
        stitched = self._stitch_target_tables(ordered_dfs)

        # 4) Return as if they are separate logical tables (often just 1 final table)
        results = []
        for idx, df in enumerate(stitched, start=1):
            results.append({"page": idx, "table_num": idx, "data": df})

        return results    
    
    def __init__(self, input_folder, output_folder):
        self.input_folder = input_folder
        self.output_folder = output_folder
        os.makedirs(output_folder, exist_ok=True)
        
    def extract_tables_with_pdfplumber(self, pdf_path):
        """Extract tables using pdfplumber"""
        tables = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    # Try to extract tables from the page
                    page_tables = page.extract_tables()
                    if page_tables:
                        for table_num, table in enumerate(page_tables):
                            # Convert to DataFrame
                            df = pd.DataFrame(table[1:], columns=table[0])
                            if not df.empty:
                                tables.append({
                                    'page': page_num + 1,
                                    'table_num': table_num + 1,
                                    'data': df
                                })
        except Exception as e:
            logger.error(f"Error extracting tables with pdfplumber from {pdf_path}: {str(e)}")
        return tables
    
    def extract_tables_with_camelot(self, pdf_path):
        """Extract tables using camelot"""
        tables = []
        try:
            # Try different flavors for different PDF formats
            for flavor in ['stream', 'lattice']:
                try:
                    camelot_tables = camelot.read_pdf(pdf_path, flavor=flavor, pages='all')
                    for i, table in enumerate(camelot_tables):
                        if not table.df.empty:
                            tables.append({
                                'page': table.page,
                                'table_num': i + 1,
                                'data': table.df
                            })
                    if tables:  # If we found tables with this flavor, break
                        break
                except Exception as e:
                    continue
        except Exception as e:
            logger.error(f"Error extracting tables with camelot from {pdf_path}: {str(e)}")
        return tables
    
    def is_financial_table(self, df):
        """Check if the table looks like a financial transaction table"""
        if df.empty or len(df.columns) < 3:
            return False
        
        # Check for common financial table column names
        financial_keywords = ['date', 'transaction', 'description', 'amount', 'debit', 'credit']
        first_row = ' '.join(str(cell).lower() for cell in df.iloc[0] if pd.notna(cell))
        
        return any(keyword in first_row for keyword in financial_keywords)
    
    def clean_table(self, df):
        """Clean extracted table data"""
        # Remove empty rows and columns
        df = df.dropna(how='all').dropna(axis=1, how='all')
        
        # Reset index
        df = df.reset_index(drop=True)
        
        # Try to promote first row to header if it looks like column names
        first_row_str = ' '.join(str(cell).lower() for cell in df.iloc[0] if pd.notna(cell))
        if any(keyword in first_row_str for keyword in ['date', 'description', 'amount']):
            df.columns = df.iloc[0]
            df = df[1:].reset_index(drop=True)
        
        return df
    
    def process_pdf(self, pdf_path):
        """Process a single PDF file"""
        filename = os.path.basename(pdf_path)
        logger.info(f"Processing {filename}")
        
        # Try both extraction methods
        tables_plumber = self.extract_tables_with_pdfplumber(pdf_path)
        tables_camelot = self.extract_tables_with_camelot(pdf_path)
        
        # Combine results
        all_tables = tables_plumber + tables_camelot
        
        # Filter for financial tables and clean them
        financial_tables = []
        for table in all_tables:
            if self.is_financial_table(table['data']):
                cleaned_table = self.clean_table(table['data'])
                if not cleaned_table.empty:
                    financial_tables.append({
                        'page': table['page'],
                        'table_num': table['table_num'],
                        'data': cleaned_table
                    })
        
        return financial_tables
    
    def save_tables(self, pdf_filename, tables):
        """Save extracted tables to CSV files"""
        base_name = os.path.splitext(pdf_filename)[0]
        output_files = []
        
        for i, table in enumerate(tables):
            output_filename = f"{base_name}_page{table['page']}_table{table['table_num']}.csv"
            output_path = os.path.join(self.output_folder, output_filename)
            table['data'].to_csv(output_path, index=False)
            output_files.append(output_path)
            logger.info(f"Saved table to {output_filename}")
        
        return output_files
    
    def process_folder(self):
        """Process all PDF files in the input folder"""
        pdf_files = [f for f in os.listdir(self.input_folder) if f.lower().endswith('.pdf')]
        
        if not pdf_files:
            logger.warning(f"No PDF files found in {self.input_folder}")
            return
        
        logger.info(f"Found {len(pdf_files)} PDF files to process")
        
        all_results = {}
        for pdf_file in pdf_files:
            pdf_path = os.path.join(self.input_folder, pdf_file)
            tables = self.process_pdf(pdf_path)
            
            if tables:
                saved_files = self.save_tables(pdf_file, tables)
                all_results[pdf_file] = {
                    'tables_found': len(tables),
                    'output_files': saved_files
                }
            else:
                logger.warning(f"No financial tables found in {pdf_file}")
                all_results[pdf_file] = {
                    'tables_found': 0,
                    'output_files': []
                }
        
        # Generate a summary report
        self.generate_summary_report(all_results)
        return all_results
    
    def generate_summary_report(self, results):
        """Generate a summary report of the processing"""
        report_path = os.path.join(self.output_folder, "processing_summary.txt")
        
        with open(report_path, 'w') as f:
            f.write(f"Visa Statement Processing Summary\n")
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 50 + "\n\n")
            
            total_files = len(results)
            total_tables = sum(data['tables_found'] for data in results.values())
            
            f.write(f"Total PDF files processed: {total_files}\n")
            f.write(f"Total tables extracted: {total_tables}\n\n")
            
            f.write("File-by-file results:\n")
            for filename, data in results.items():
                f.write(f"{filename}: {data['tables_found']} tables extracted\n")
                for output_file in data['output_files']:
                    f.write(f"  -> {os.path.basename(output_file)}\n")

def main():
    # Configuration - adjust these paths as needed
    input_folder = "pdf_statements"  # Folder containing your PDF files
    output_folder = "extracted_tables"  # Folder where CSV files will be saved
    
    # Create processor and run
    processor = VisaPDFProcessor(input_folder, output_folder)
    results = processor.process_folder()
    
    print(f"Processing complete. Results saved in {output_folder}")

if __name__ == "__main__":
    main()