import os
import pandas as pd
import pdfplumber
import camelot
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
from datetime import datetime
import logging

# Try to import optional libraries
try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False

try:
    import tabula
    TABULA_AVAILABLE = True
except ImportError:
    TABULA_AVAILABLE = False

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TargetedVisaPDFExtractor:
    def __init__(self, root):
        self.root = root
        self.root.title("Targeted Visa PDF Extractor")
        self.root.geometry("900x700")
        
        # Specific Visa column patterns to look for
        self.visa_columns = [
            "trans date", "post date", "description", "spend categories", "amount",
            "transaction date", "posting date", "merchant", "category", "debit/credit",
            "trans.date", "post.date", "amount($)", "amount usd", "debit", "credit"
        ]
        
        self.setup_ui()
    
    def setup_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(6, weight=1)
        
        # Title
        title_label = ttk.Label(main_frame, text="Targeted Visa PDF Extractor", font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=3, pady=10)
        
        # Instructions
        instructions = ttk.Label(main_frame, 
                                text="This tool specifically targets Visa transaction tables with columns like 'Trans Date', 'Post Date', 'Description', 'Spend Categories', and 'Amount'.",
                                wraplength=700)
        instructions.grid(row=1, column=0, columnspan=3, pady=5)
        
        # PDF file selection
        ttk.Label(main_frame, text="PDF File:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.pdf_file_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.pdf_file_var, width=60).grid(row=2, column=1, sticky=(tk.W, tk.E), padx=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_pdf_file).grid(row=2, column=2, padx=5)
        
        # Output folder selection
        ttk.Label(main_frame, text="Output Folder:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.output_folder_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.output_folder_var, width=60).grid(row=3, column=1, sticky=(tk.W, tk.E), padx=5)
        ttk.Button(main_frame, text="Browse", command=self.browse_output_folder).grid(row=3, column=2, padx=5)
        
        # Process button
        self.process_btn = ttk.Button(main_frame, text="Extract Transactions", command=self.start_processing)
        self.process_btn.grid(row=4, column=0, columnspan=3, pady=10)
        
        # Progress bar
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.grid(row=5, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
        
        # Results area
        results_frame = ttk.LabelFrame(main_frame, text="Processing Results", padding="5")
        results_frame.grid(row=6, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.N, tk.S), pady=10)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        
        self.results_text = scrolledtext.ScrolledText(results_frame, width=80, height=20)
        self.results_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready to process a PDF file")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=7, column=0, sticky=(tk.W, tk.E))
    
    def browse_pdf_file(self):
        file_path = filedialog.askopenfilename(
            title="Select PDF Statement",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if file_path:
            self.pdf_file_var.set(file_path)
            if not self.output_folder_var.get():
                self.output_folder_var.set(os.path.dirname(file_path))
    
    def browse_output_folder(self):
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_folder_var.set(folder)
    
    def start_processing(self):
        pdf_path = self.pdf_file_var.get()
        output_folder = self.output_folder_var.get()
        
        if not pdf_path or not os.path.isfile(pdf_path):
            messagebox.showerror("Error", "Please select a valid PDF file")
            return
        
        if not output_folder or not os.path.isdir(output_folder):
            messagebox.showerror("Error", "Please select a valid output folder")
            return
        
        # Disable button during processing
        self.process_btn.config(state=tk.DISABLED)
        self.progress.start(10)
        self.status_var.set("Processing...")
        self.results_text.delete(1.0, tk.END)
        self.results_text.insert(tk.END, f"Processing PDF file: {os.path.basename(pdf_path)}\n")
        
        # Run processing in a separate thread
        thread = threading.Thread(target=self.process_pdf, args=(pdf_path, output_folder))
        thread.daemon = True
        thread.start()
    
    def extract_visa_tables(self, pdf_path):
        """Extract tables specifically looking for Visa transaction tables"""
        all_tables = []
        
        # Try PyMuPDF (fitz) if available
        if FITZ_AVAILABLE:
            try:
                self.update_status("Trying PyMuPDF (fitz) extraction...")
                doc = fitz.open(pdf_path)
                total_pages = len(doc)
                
                for page_num in range(total_pages):
                    self.update_status(f"Scanning page {page_num+1}/{total_pages} with PyMuPDF...")
                    page = doc.load_page(page_num)
                    
                    # Try to extract tables using PyMuPDF's table finding capabilities
                    tabs = page.find_tables()
                    if tabs.tables:
                        for table_num, table in enumerate(tabs.tables):
                            df = table.to_pandas()
                            
                            # Check if this is a Visa transaction table
                            if self.is_visa_transaction_table(df):
                                # Add source information
                                df['Source Page'] = page_num + 1
                                df['Source PDF'] = os.path.basename(pdf_path)
                                all_tables.append(df)
                                self.results_text.insert(tk.END, f"Found Visa table on page {page_num+1}, table {table_num+1} with PyMuPDF\n")
                
                doc.close()
            except Exception as e:
                self.results_text.insert(tk.END, f"PyMuPDF error: {str(e)}\n")
        else:
            self.results_text.insert(tk.END, "PyMuPDF not available, skipping...\n")
        
        # Try tabula-py if available
        if TABULA_AVAILABLE and not all_tables:
            try:
                self.update_status("Trying tabula-py extraction...")
                # Try different area configurations
                area_configs = [
                    None,  # Default area (whole page)
                    [100, 0, 500, 600],  # Top part of page
                    [200, 0, 600, 600],  # Middle part of page
                ]
                
                for area in area_configs:
                    try:
                        dfs = tabula.read_pdf(
                            pdf_path, 
                            pages='all', 
                            multiple_tables=True,
                            area=area,
                            guess=False,
                            silent=True
                        )
                        
                        for i, df in enumerate(dfs):
                            if not df.empty:
                                # Check if this is a Visa transaction table
                                if self.is_visa_transaction_table(df):
                                    # Add source information (tabula doesn't provide page info directly)
                                    df['Source PDF'] = os.path.basename(pdf_path)
                                    all_tables.append(df)
                                    self.results_text.insert(tk.END, f"Found Visa table with tabula-py (config {area_configs.index(area)})\n")
                                    break  # Found a table, no need to try other configs
                        
                        if all_tables:  # If we found tables with this config, break
                            break
                    except Exception as e:
                        continue
            except Exception as e:
                self.results_text.insert(tk.END, f"Tabula-py error: {str(e)}\n")
        else:
            self.results_text.insert(tk.END, "Tabula-py not available, skipping...\n")
        
        # If we didn't find tables with PyMuPDF or tabula, try pdfplumber
        if not all_tables:
            try:
                self.update_status("Trying pdfplumber extraction...")
                with pdfplumber.open(pdf_path) as pdf:
                    total_pages = len(pdf.pages)
                    
                    for page_num, page in enumerate(pdf.pages):
                        self.update_status(f"Scanning page {page_num+1}/{total_pages} with pdfplumber...")
                        
                        # Try different table extraction settings
                        table_settings = [
                            {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
                            {"vertical_strategy": "lines", "horizontal_strategy": "text"},
                            {"vertical_strategy": "text", "horizontal_strategy": "lines"}
                        ]
                        
                        for settings in table_settings:
                            try:
                                page_tables = page.extract_tables(settings)
                                
                                for table_num, table in enumerate(page_tables):
                                    if not table or len(table) < 2:
                                        continue
                                    
                                    # Convert to DataFrame
                                    df = pd.DataFrame(table)
                                    
                                    # Check if this is a Visa transaction table
                                    if self.is_visa_transaction_table(df):
                                        # Add source information
                                        df['Source Page'] = page_num + 1
                                        df['Source PDF'] = os.path.basename(pdf_path)
                                        all_tables.append(df)
                                        self.results_text.insert(tk.END, f"Found Visa table on page {page_num+1}, table {table_num+1} with pdfplumber\n")
                            except Exception as e:
                                continue
            except Exception as e:
                self.results_text.insert(tk.END, f"PDFPlumber error: {str(e)}\n")
        
        # If we still didn't find any tables, try camelot
        if not all_tables:
            try:
                self.update_status("Trying camelot extraction...")
                # Try different flavors and settings for different PDF formats
                for flavor in ['stream', 'lattice']:
                    try:
                        # Try different line scale values
                        for line_scale in [15, 20, 25, 30]:
                            try:
                                camelot_tables = camelot.read_pdf(
                                    pdf_path, 
                                    flavor=flavor, 
                                    pages='all',
                                    line_scale=line_scale,
                                    strip_text='\n'
                                )
                                
                                for table in camelot_tables:
                                    if not table.df.empty:
                                        df = table.df
                                        
                                        # Check if this is a Visa transaction table
                                        if self.is_visa_transaction_table(df):
                                            # Add source information
                                            df['Source Page'] = table.page
                                            df['Source PDF'] = os.path.basename(pdf_path)
                                            all_tables.append(df)
                                            self.results_text.insert(tk.END, f"Found Visa table on page {table.page} with camelot\n")
                                
                                if all_tables:  # If we found tables with this flavor, break
                                    break
                            except Exception as e:
                                continue
                        if all_tables:
                            break
                    except Exception as e:
                        continue
            except Exception as e:
                self.results_text.insert(tk.END, f"Camelot error: {str(e)}\n")
        
        return all_tables
    
    def is_visa_transaction_table(self, df):
        """Check if the table is a Visa transaction table with specific column patterns"""
        if df.empty or len(df.columns) < 3:
            return False
        
        # Convert all values to strings and lowercase for comparison
        df_str = df.astype(str).apply(lambda x: x.str.lower())
        
        # Check multiple rows for Visa-specific column headers
        for row_idx in range(min(5, len(df_str))):
            row_values = df_str.iloc[row_idx].tolist()
            row_text = " ".join(row_values)
            
            # Count how many Visa column patterns appear in this row
            matching_columns = 0
            for col_pattern in self.visa_columns:
                if any(col_pattern in cell for cell in row_values):
                    matching_columns += 1
            
            # If we found at least 2 matching column patterns, this might be our header row
            if matching_columns >= 2:
                return True
        
        # Additional check for transaction-like data patterns
        if len(df) > 5:
            # Check if we have date-like values in likely columns
            date_like_count = 0
            for col_idx in range(min(3, len(df.columns))):  # Check first 3 columns
                for row_idx in range(1, min(10, len(df))):  # Check more rows
                    cell_value = str(df.iloc[row_idx, col_idx])
                    if re.match(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', cell_value):
                        date_like_count += 1
                        break
            
            # Check if we have amount-like values in likely columns
            amount_like_count = 0
            for col_idx in range(max(0, len(df.columns)-2), len(df.columns)):  # Check last 2 columns
                for row_idx in range(1, min(10, len(df))):  # Check more rows
                    cell_value = str(df.iloc[row_idx, col_idx]).replace(',', '').replace('$', '')
                    if re.match(r'-?\d+\.\d{2}', cell_value):
                        amount_like_count += 1
                        break
            
            # If we have both date-like and amount-like values, it's likely a transaction table
            if date_like_count >= 1 and amount_like_count >= 1:
                return True
        
        return False
    
    def extract_header_row(self, df):
        """Find the header row in a potential Visa transaction table"""
        df_str = df.astype(str).apply(lambda x: x.str.lower())
        
        # Check multiple rows for Visa-specific column headers
        for row_idx in range(min(5, len(df_str))):
            row_values = df_str.iloc[row_idx].tolist()
            
            # Count how many Visa column patterns appear in this row
            matching_columns = 0
            for col_pattern in self.visa_columns:
                if any(col_pattern in cell for cell in row_values):
                    matching_columns += 1
            
            # If we found at least 2 matching column patterns, this is likely our header row
            if matching_columns >= 2:
                return row_idx
        
        # If we didn't find a clear header, assume the first row is the header
        return 0
    
    def clean_visa_table(self, df):
        """Clean and standardize a Visa transaction table"""
        if df.empty:
            return df
        
        # Make a copy to avoid modifying the original
        df = df.copy()
        
        # Find the header row
        header_row = self.extract_header_row(df)
        
        # Set the header row as column names
        df.columns = df.iloc[header_row]
        
        # Remove the header row and any rows above it
        df = df[header_row+1:].reset_index(drop=True)
        
        # Remove empty rows and columns
        df = df.dropna(how='all').dropna(axis=1, how='all')
        
        # Reset index
        df = df.reset_index(drop=True)
        
        # Clean up column names
        df.columns = [str(col).strip() for col in df.columns]
        
        # Remove any rows that don't have transaction data
        # Check if we have at least one date-like value and one amount-like value
        valid_rows = []
        for idx, row in df.iterrows():
            has_date = False
            has_amount = False
            
            # Check for date in first few columns
            for col_idx in range(min(3, len(df.columns))):
                cell_value = str(row.iloc[col_idx])
                if re.match(r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', cell_value):
                    has_date = True
                    break
            
            # Check for amount in last few columns
            for col_idx in range(max(0, len(df.columns)-2), len(df.columns)):
                cell_value = str(row.iloc[col_idx]).replace(',', '').replace('$', '')
                if re.match(r'-?\d+\.\d{2}', cell_value):
                    has_amount = True
                    break
            
            if has_date or has_amount:  # Changed from AND to OR to be more lenient
                valid_rows.append(idx)
        
        # Keep only valid rows
        df = df.loc[valid_rows].reset_index(drop=True)
        
        return df
    
    def process_pdf(self, pdf_path, output_folder):
        try:
            self.results_text.insert(tk.END, "Scanning for Visa transaction tables...\n")
            
            # Extract tables specifically looking for Visa patterns
            tables = self.extract_visa_tables(pdf_path)
            
            if tables:
                self.results_text.insert(tk.END, f"Found {len(tables)} potential tables. Cleaning...\n")
                
                # Debug: Show raw table data
                for i, df in enumerate(tables):
                    self.results_text.insert(tk.END, f"\nRaw table {i+1} (first 5 rows):\n")
                    self.results_text.insert(tk.END, df.head().to_string() + "\n")
                    self.results_text.insert(tk.END, f"Table shape: {df.shape}\n")
                    self.results_text.insert(tk.END, f"Columns: {list(df.columns)}\n")
                
                # Clean all tables
                cleaned_tables = []
                for i, df in enumerate(tables):
                    try:
                        cleaned_df = self.clean_visa_table(df)
                        if not cleaned_df.empty:
                            cleaned_tables.append(cleaned_df)
                            self.results_text.insert(tk.END, f"Table {i+1}: {len(cleaned_df)} valid transactions found\n")
                            
                            # Debug: Show cleaned table data
                            self.results_text.insert(tk.END, f"Cleaned table {i+1} (first 5 rows):\n")
                            self.results_text.insert(tk.END, cleaned_df.head().to_string() + "\n")
                        else:
                            self.results_text.insert(tk.END, f"Table {i+1}: No valid transactions after cleaning\n")
                            # Debug: Show why cleaning failed
                            self.results_text.insert(tk.END, f"Raw table shape: {df.shape}\n")
                            self.results_text.insert(tk.END, f"Raw table columns: {list(df.columns)}\n")
                    except Exception as e:
                        self.results_text.insert(tk.END, f"Error cleaning table {i+1}: {str(e)}\n")
                        # Also output the first 10 rows for error case
                        self.results_text.insert(tk.END, f"Raw table {i+1} first 10 rows:\n")
                        self.results_text.insert(tk.END, df.head(10).to_string() + "\n")
                
                if cleaned_tables:
                    # Combine all tables
                    combined_df = pd.concat(cleaned_tables, ignore_index=True)
                    
                    # Save to CSV
                    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
                    output_path = os.path.join(output_folder, f"{pdf_name}_transactions.csv")
                    combined_df.to_csv(output_path, index=False)
                    
                    self.results_text.insert(tk.END, f"\nSuccess! Extracted {len(combined_df)} transactions total\n")
                    self.results_text.insert(tk.END, f"Saved to: {output_path}\n")
                    
                    # Show a sample of the data
                    self.results_text.insert(tk.END, "\nFirst few rows:\n")
                    self.results_text.insert(tk.END, combined_df.head().to_string() + "\n")
                    
                    # Show column information
                    self.results_text.insert(tk.END, f"\nColumns found: {list(combined_df.columns)}\n")
                    
                    self.update_status(f"Done! Extracted {len(combined_df)} transactions")
                    messagebox.showinfo("Success", f"Extracted {len(combined_df)} transactions from the PDF!")
                else:
                    self.results_text.insert(tk.END, "\nFound tables but couldn't extract transaction data.\n")
                    self.results_text.insert(tk.END, "This might be due to:\n")
                    self.results_text.insert(tk.END, "1. Table structure is different than expected\n")
                    self.results_text.insert(tk.END, "2. The PDF might be scanned (image-based) rather than text-based\n")
                    self.results_text.insert(tk.END, "3. Column names don't match the expected patterns\n")
                    self.update_status("No transaction data found")
                    messagebox.showinfo("No Data", "Found tables but couldn't extract transaction data.")
            else:
                self.results_text.insert(tk.END, "\nNo Visa transaction tables found in the PDF.\n")
                self.results_text.insert(tk.END, "This might be due to:\n")
                self.results_text.insert(tk.END, "1. The PDF might be scanned (image-based) rather than text-based\n")
                self.results_text.insert(tk.END, "2. Table structure is different than expected\n")
                self.results_text.insert(tk.END, "3. Try using OCR software to convert the PDF to text first\n")
                self.update_status("No Visa transaction tables found")
                messagebox.showinfo("No Data", "No Visa transaction tables were found in the PDF.")
            
        except Exception as e:
            error_msg = f"Error processing PDF: {str(e)}"
            self.results_text.insert(tk.END, f"\nError: {error_msg}\n")
            self.update_status("Error occurred")
            messagebox.showerror("Error", error_msg)
        
        finally:
            self.progress.stop()
            self.root.after(0, lambda: self.process_btn.config(state=tk.NORMAL))
    
    def update_status(self, message):
        self.root.after(0, lambda: self.status_var.set(message))


if __name__ == "__main__":
    root = tk.Tk()
    app = TargetedVisaPDFExtractor(root)
    root.mainloop()