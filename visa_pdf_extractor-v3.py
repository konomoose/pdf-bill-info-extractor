from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from src.bill_extractor.pdf_processor import (
    BatchResult,
    PDFProcessingError,
    VisaPDFProcessor,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
    ProfileError,
    discover_profiles,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TargetedVisaPDFExtractor:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("PDF Bill Info Extractor")
        self.root.geometry("980x800")

        try:
            profiles = discover_profiles()
        except ProfileError as exc:
            messagebox.showerror("Profile Error", str(exc))
            raise

        if not profiles:
            raise PDFProcessingError("No extraction profiles were found in config/profiles.")

        self.profiles_by_name = {
            profile.display_name: profile for profile in profiles
        }
        self.active_profile = profiles[0]
        self.processor = VisaPDFProcessor(profile=self.active_profile)

        self.setup_ui()
        self._apply_profile(self.active_profile)

    def setup_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(10, weight=1)

        ttk.Label(
            main_frame,
            text="PDF Bill Info Extractor",
            font=("Arial", 16, "bold"),
        ).grid(row=0, column=0, columnspan=3, pady=10)

        self.profile_info_var = tk.StringVar()
        ttk.Label(
            main_frame,
            textvariable=self.profile_info_var,
            wraplength=780,
        ).grid(row=1, column=0, columnspan=3, pady=5)

        ttk.Label(main_frame, text="Profile:").grid(
            row=2, column=0, sticky=tk.W, pady=5
        )
        self.profile_var = tk.StringVar(value=self.active_profile.display_name)
        profile_combo = ttk.Combobox(
            main_frame,
            textvariable=self.profile_var,
            values=list(self.profiles_by_name),
            state="readonly",
            width=40,
        )
        profile_combo.grid(row=2, column=1, sticky=tk.W, padx=5)
        profile_combo.bind("<<ComboboxSelected>>", self._profile_selected)

        ttk.Label(main_frame, text="Input Mode:").grid(
            row=3, column=0, sticky=tk.W, pady=5
        )
        self.input_mode_var = tk.StringVar(value="file")
        input_mode_frame = ttk.Frame(main_frame)
        input_mode_frame.grid(row=3, column=1, columnspan=2, sticky=tk.W)
        ttk.Radiobutton(
            input_mode_frame,
            text="Single PDF",
            variable=self.input_mode_var,
            value="file",
        ).grid(row=0, column=0, padx=(0, 15))
        ttk.Radiobutton(
            input_mode_frame,
            text="PDF folder",
            variable=self.input_mode_var,
            value="folder",
        ).grid(row=0, column=1)

        ttk.Label(main_frame, text="PDF File:").grid(
            row=4, column=0, sticky=tk.W, pady=5
        )
        self.pdf_file_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.pdf_file_var, width=60).grid(
            row=4, column=1, sticky=(tk.W, tk.E), padx=5
        )
        ttk.Button(main_frame, text="Browse", command=self.browse_pdf_file).grid(
            row=4, column=2, padx=5
        )

        ttk.Label(main_frame, text="PDF Folder:").grid(
            row=5, column=0, sticky=tk.W, pady=5
        )
        self.pdf_folder_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.pdf_folder_var, width=60).grid(
            row=5, column=1, sticky=(tk.W, tk.E), padx=5
        )
        ttk.Button(main_frame, text="Browse", command=self.browse_pdf_folder).grid(
            row=5, column=2, padx=5
        )

        ttk.Label(main_frame, text="Output Folder:").grid(
            row=6, column=0, sticky=tk.W, pady=5
        )
        self.output_folder_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.output_folder_var, width=60).grid(
            row=6, column=1, sticky=(tk.W, tk.E), padx=5
        )
        ttk.Button(main_frame, text="Browse", command=self.browse_output_folder).grid(
            row=6, column=2, padx=5
        )

        self.process_btn = ttk.Button(
            main_frame,
            text="Extract Transactions",
            command=self.start_processing,
        )
        self.process_btn.grid(row=7, column=0, columnspan=3, pady=10)

        self.folder_mode_info_var = tk.StringVar()
        ttk.Label(
            main_frame,
            textvariable=self.folder_mode_info_var,
            wraplength=780,
        ).grid(row=8, column=0, columnspan=3, pady=(0, 5))

        self.progress = ttk.Progressbar(main_frame, mode="indeterminate")
        self.progress.grid(
            row=9, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5
        )

        results_frame = ttk.LabelFrame(
            main_frame, text="Processing Results", padding="5"
        )
        results_frame.grid(
            row=10,
            column=0,
            columnspan=3,
            sticky=(tk.W, tk.E, tk.N, tk.S),
            pady=10,
        )
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        self.results_text = scrolledtext.ScrolledText(
            results_frame, width=90, height=22
        )
        self.results_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(
            self.root,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
        ).grid(row=7, column=0, sticky=(tk.W, tk.E))

    def _profile_selected(self, _event: object | None = None) -> None:
        profile = self.profiles_by_name[self.profile_var.get()]
        self._apply_profile(profile)

    def _apply_profile(self, profile: ExtractionProfile) -> None:
        self.active_profile = profile
        self.processor = VisaPDFProcessor(profile=profile)

        input_folder = profile.resolve_input_folder()
        output_folder = profile.resolve_output_folder()
        output_folder.mkdir(parents=True, exist_ok=True)

        self.pdf_folder_var.set(str(input_folder))
        self.output_folder_var.set(str(output_folder))
        self.profile_info_var.set(
            f"{profile.display_name} profile v{profile.profile_version}: extracts "
            "Trans date, Post date, Description, Spend Categories, and Amount($). "
            "The CIBC CreditSmart Spend Report is ignored."
        )
        recursive_text = "including subfolders" if profile.recursive else "directly inside the folder"
        self.folder_mode_info_var.set(
            f"Folder mode uses {profile.file_pattern} files {recursive_text}. "
            "Each statement receives its own CSV."
        )
        self.status_var.set(f"Ready: {profile.display_name}")

    def browse_pdf_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select PDF Statement",
            initialdir=str(self.active_profile.resolve_input_folder()),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if not file_path:
            return

        self.input_mode_var.set("file")
        self.pdf_file_var.set(file_path)

    def browse_pdf_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select Folder Containing PDF Statements",
            initialdir=str(self.active_profile.resolve_input_folder()),
        )
        if not folder:
            return

        self.input_mode_var.set("folder")
        self.pdf_folder_var.set(folder)

    def browse_output_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select Output Folder",
            initialdir=str(self.active_profile.resolve_output_folder()),
        )
        if folder:
            self.output_folder_var.set(folder)

    def start_processing(self) -> None:
        input_mode = self.input_mode_var.get()
        output_folder = self.output_folder_var.get().strip()

        if not output_folder:
            messagebox.showerror("Error", "Please select an output folder.")
            return

        Path(output_folder).mkdir(parents=True, exist_ok=True)

        if input_mode == "file":
            pdf_path = self.pdf_file_var.get().strip()
            if not pdf_path or not os.path.isfile(pdf_path):
                messagebox.showerror("Error", "Please select a valid PDF file.")
                return
            input_path = pdf_path
            opening_message = f"Processing PDF file: {Path(pdf_path).name}\n"
        else:
            folder_path = self.pdf_folder_var.get().strip()
            if not folder_path or not os.path.isdir(folder_path):
                messagebox.showerror(
                    "Error", "Please select a valid folder containing PDF files."
                )
                return
            input_path = folder_path
            opening_message = f"Processing PDF folder: {folder_path}\n"

        self.process_btn.config(state=tk.DISABLED)
        self.progress.start(10)
        self.status_var.set("Processing...")
        self.results_text.delete("1.0", tk.END)
        self._append_result(
            f"Profile: {self.active_profile.display_name} "
            f"({self.active_profile.profile_id})\n"
        )
        self._append_result(opening_message)

        worker = threading.Thread(
            target=self._processing_worker,
            args=(input_mode, input_path, output_folder),
            daemon=True,
        )
        worker.start()

    def _processing_worker(
        self, input_mode: str, input_path: str, output_folder: str
    ) -> None:
        try:
            if input_mode == "file":
                result, csv_path = self.processor.process_pdf(
                    input_path, output_folder
                )
                total_amount = (
                    result.transactions["Amount($)"]
                    .str.replace(",", "", regex=False)
                    .astype(float)
                    .sum()
                )
                messages = [
                    "\nSUCCESS",
                    f"Profile: {self.active_profile.display_name}",
                    f"PDF: {Path(input_path).name}",
                    f"Pages: {', '.join(map(str, result.source_pages))}",
                    f"Transactions: {len(result.transactions)}",
                    f"Transaction total: ${total_amount:,.2f}",
                    "Ignored the CIBC CreditSmart Spend Report.",
                    f"Saved to: {csv_path}",
                ]
                self.root.after(
                    0,
                    self._single_processing_succeeded,
                    messages,
                    len(result.transactions),
                )
            else:
                batch_result = self.processor.process_folder(
                    input_path, output_folder
                )
                self.root.after(0, self._batch_processing_succeeded, batch_result)

        except PDFProcessingError as exc:
            logger.exception("PDF processing failed")
            self.root.after(0, self._processing_failed, str(exc))
        except Exception as exc:
            logger.exception("Unexpected processing error")
            self.root.after(0, self._processing_failed, f"Unexpected error: {exc}")

    def _single_processing_succeeded(
        self, messages: list[str], transaction_count: int
    ) -> None:
        for message in messages:
            self._append_result(f"{message}\n")

        self.status_var.set(f"Done: {transaction_count} transactions extracted")
        self.progress.stop()
        self.process_btn.config(state=tk.NORMAL)
        messagebox.showinfo(
            "Success", f"Extracted {transaction_count} transactions."
        )

    def _batch_processing_succeeded(self, result: BatchResult) -> None:
        self._append_result("\nBATCH RESULTS\n")
        self._append_result(f"Profile: {result.profile_name} ({result.profile_id})\n")

        for item in result.files:
            if item.status == "Success":
                pages = ", ".join(map(str, item.source_pages))
                self._append_result(
                    f"SUCCESS: {item.pdf_file.name} | "
                    f"{item.transaction_count} transactions | pages {pages}\n"
                )
                self._append_result(f"         {item.output_csv}\n")
            else:
                self._append_result(
                    f"FAILED:  {item.pdf_file.name} | {item.error}\n"
                )

        self._append_result("\nSUMMARY\n")
        self._append_result(f"PDF files found: {len(result.files)}\n")
        self._append_result(f"Successful: {result.successful_count}\n")
        self._append_result(f"Failed: {result.failed_count}\n")
        self._append_result(
            f"Total transactions extracted: {result.transaction_count}\n"
        )
        self._append_result(f"Batch summary CSV: {result.summary_csv}\n")

        self.status_var.set(
            f"Done: {result.successful_count} succeeded, "
            f"{result.failed_count} failed"
        )
        self.progress.stop()
        self.process_btn.config(state=tk.NORMAL)
        messagebox.showinfo(
            "Batch Complete",
            f"Successful: {result.successful_count}\n"
            f"Failed: {result.failed_count}\n"
            f"Transactions: {result.transaction_count}",
        )

    def _processing_failed(self, error_message: str) -> None:
        self._append_result(f"\nError: {error_message}\n")
        self.status_var.set("Processing failed")
        self.progress.stop()
        self.process_btn.config(state=tk.NORMAL)
        messagebox.showerror("Error", error_message)

    def _append_result(self, text: str) -> None:
        self.results_text.insert(tk.END, text)
        self.results_text.see(tk.END)


def main() -> None:
    root = tk.Tk()
    try:
        TargetedVisaPDFExtractor(root)
    except (PDFProcessingError, ProfileError) as exc:
        logger.error("Application startup failed: %s", exc)
        messagebox.showerror("Startup Error", str(exc))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
