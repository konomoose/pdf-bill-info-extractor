from __future__ import annotations

import logging
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from src.bill_extractor.pdf_processor import (
    PDFProcessingError,
)
from src.bill_extractor.institution_collector import (
    InstitutionCollectionError,
    InstitutionCollectionResult,
    collect_profile_institution_statement_csvs,
)
from src.bill_extractor.profile_loader import (
    ExtractionProfile,
    ProfileError,
    discover_profiles,
)
from src.bill_extractor.workflow import (
    WorkflowError,
    WorkflowResult,
    run_selected_extraction_workflow,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def workflow_result_messages(
    result: WorkflowResult,
) -> list[str]:
    """Build privacy-safe GUI messages for one workflow run."""
    messages = [
        "",
        "WORKFLOW RESULTS",
    ]

    for item in result.files:
        pdf_name = (
            item.pdf_file.name
            if item.pdf_file is not None
            else "(no PDF)"
        )

        if item.status == "Success":
            pages = (
                ", ".join(
                    map(str, item.source_pages)
                )
                if item.source_pages
                else "none"
            )

            messages.append(
                f"SUCCESS: {pdf_name} | "
                f"{item.transaction_count} transactions | "
                f"pages {pages}"
            )

            if item.raw_csv is not None:
                messages.append(
                    f"         Raw CSV: {item.raw_csv}"
                )

            if item.normalized_csv is not None:
                messages.append(
                    "         Normalized CSV: "
                    f"{item.normalized_csv}"
                )

        elif item.status == "Skipped":
            messages.append(
                f"SKIPPED: {pdf_name} | "
                f"{item.error or 'No details available.'}"
            )

        else:
            messages.append(
                f"FAILED:  {pdf_name} | "
                f"{item.error or 'No details available.'}"
            )

    messages.extend(
        [
            "",
            "YEARLY OUTPUTS",
        ]
    )

    if result.yearly_outputs:
        for yearly in result.yearly_outputs:
            messages.append(
                f"{yearly.year}: "
                f"{yearly.transaction_count} transactions | "
                f"{yearly.csv_path}"
            )
    else:
        messages.append(
            "No yearly CSVs were created."
        )

    messages.extend(
        [
            "",
            "SUMMARY",
            f"Files examined: {len(result.files)}",
            (
                "Successful: "
                f"{result.successful_count}"
            ),
            f"Skipped: {result.skipped_count}",
            f"Failed: {result.failed_count}",
            (
                "Transactions extracted: "
                f"{result.transaction_count}"
            ),
            f"Workflow summary CSV: {result.summary_csv}",
        ]
    )

    return messages


def institution_collection_result_messages(
    result: InstitutionCollectionResult,
) -> list[str]:
    """Build privacy-safe GUI messages for institution collection."""
    return [
        "",
        "INSTITUTION COLLECTION RESULTS",
        f"Institution: {result.institution}",
        f"Collection folder: {result.all_statements_folder}",
        (
            "Source: configured output folders for all profiles "
            "at this institution."
        ),
        (
            "Normalized statement CSVs collected: "
            f"{result.collected_count}"
        ),
        (
            "Combined transactions: "
            f"{result.combined_transaction_count}"
        ),
        f"Combined CSV: {result.combined_csv_path}",
        (
            "Stale managed files removed: "
            f"{result.stale_removed_count}"
        ),
    ]


class PDFBillExtractorApp:
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
            raise PDFProcessingError(
                "No extraction profiles were found in config/profiles."
            )

        self.profiles = profiles
        self.profiles_by_name = {
            profile.display_name: profile for profile in profiles
        }
        self.active_profile = profiles[0]

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
            wraplength=820,
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
            width=44,
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

        button_frame = ttk.Frame(
            main_frame
        )
        button_frame.grid(
            row=7,
            column=0,
            columnspan=3,
            pady=10,
        )

        self.process_btn = ttk.Button(
            button_frame,
            text="Extract Transactions",
            command=self.start_processing,
        )
        self.process_btn.grid(
            row=0,
            column=0,
            padx=(0, 10),
        )

        self.collect_btn = ttk.Button(
            button_frame,
            text="Collect Institution CSVs",
            command=self.start_collection,
        )
        self.collect_btn.grid(
            row=0,
            column=1,
        )

        self.folder_mode_info_var = tk.StringVar()
        ttk.Label(
            main_frame,
            textvariable=self.folder_mode_info_var,
            wraplength=820,
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
        self._apply_profile(self.profiles_by_name[self.profile_var.get()])

    def _apply_profile(self, profile: ExtractionProfile) -> None:
        self.active_profile = profile

        input_folder = profile.resolve_input_folder()
        output_folder = profile.resolve_output_folder()
        input_folder.mkdir(parents=True, exist_ok=True)
        output_folder.mkdir(parents=True, exist_ok=True)

        self.pdf_file_var.set("")
        self.pdf_folder_var.set(str(input_folder))
        self.output_folder_var.set(str(output_folder))

        headers = ", ".join(profile.required_headers)
        self.profile_info_var.set(
            f"{profile.display_name} profile v{profile.profile_version}. "
            f"Exported columns: {headers}."
        )

        recursive_text = (
            "including subfolders"
            if profile.recursive
            else "directly inside the selected folder"
        )
        self.folder_mode_info_var.set(
            f"Folder mode scans {profile.file_pattern} files "
            f"{recursive_text}. Raw and normalized statement CSVs, "
            "yearly consolidated CSVs, and a workflow summary are created. "
            "Collect Institution CSVs uses the configured profile output "
            "folders for this institution, not the selected extraction "
            "output folder."
        )
        self.status_var.set(f"Ready: {profile.display_name}")

    def browse_pdf_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="Select PDF Statement",
            initialdir=str(self.active_profile.resolve_input_folder()),
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")],
        )
        if file_path:
            self.input_mode_var.set("file")
            self.pdf_file_var.set(file_path)

    def browse_pdf_folder(self) -> None:
        folder = filedialog.askdirectory(
            title="Select Folder Containing PDF Statements",
            initialdir=str(self.active_profile.resolve_input_folder()),
        )
        if folder:
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
            input_path = self.pdf_file_var.get().strip()
            if not input_path or not os.path.isfile(input_path):
                messagebox.showerror("Error", "Please select a valid PDF file.")
                return
            opening_message = f"Processing PDF file: {Path(input_path).name}\n"
        else:
            input_path = self.pdf_folder_var.get().strip()
            if not input_path or not os.path.isdir(input_path):
                messagebox.showerror(
                    "Error", "Please select a valid folder containing PDF files."
                )
                return
            opening_message = f"Processing PDF folder: {input_path}\n"

        profile = self.active_profile

        self._set_buttons_state(tk.DISABLED)
        self.progress.start(10)
        self.status_var.set("Processing...")
        self.results_text.delete("1.0", tk.END)
        self._append_result(f"Profile: {profile.display_name} ({profile.profile_id})\n")
        self._append_result(opening_message)

        worker = threading.Thread(
            target=self._processing_worker,
            args=(
                profile,
                input_path,
                output_folder,
            ),
            daemon=True,
        )
        worker.start()

    def start_collection(self) -> None:
        profile = self.active_profile

        self._set_buttons_state(tk.DISABLED)
        self.progress.start(10)
        self.status_var.set("Collecting institution CSVs...")
        self.results_text.delete("1.0", tk.END)
        self._append_result(
            f"Institution: {profile.institution}\n"
        )
        self._append_result(
            "Collecting normalized statement CSVs for "
            f"{profile.institution}.\n"
        )
        self._append_result(
            "Source: configured output folders for all profiles at this "
            "institution, not the selected extraction output folder.\n"
        )

        worker = threading.Thread(
            target=self._collection_worker,
            args=(profile,),
            daemon=True,
        )
        worker.start()

    def _collection_worker(
        self,
        profile: ExtractionProfile,
    ) -> None:
        try:
            result = collect_profile_institution_statement_csvs(
                profile,
                profiles=self.profiles,
            )

            self.root.after(
                0,
                self._collection_succeeded,
                result,
            )

        except (
            InstitutionCollectionError,
            ProfileError,
            OSError,
        ) as exc:
            logger.exception(
                "Institution collection failed"
            )
            self.root.after(
                0,
                self._processing_failed,
                str(exc),
            )

        except Exception as exc:
            logger.exception(
                "Unexpected institution collection error"
            )
            self.root.after(
                0,
                self._processing_failed,
                f"Unexpected error: {exc}",
            )

    def _processing_worker(
        self,
        profile: ExtractionProfile,
        input_path: str,
        output_folder: str,
    ) -> None:
        try:
            result = run_selected_extraction_workflow(
                profile,
                input_path,
                output_folder,
                summary_root=output_folder,
            )

            self.root.after(
                0,
                self._workflow_processing_succeeded,
                result,
            )

        except (
            WorkflowError,
            PDFProcessingError,
        ) as exc:
            logger.exception(
                "Extraction workflow failed"
            )
            self.root.after(
                0,
                self._processing_failed,
                str(exc),
            )

        except Exception as exc:
            logger.exception(
                "Unexpected workflow error"
            )
            self.root.after(
                0,
                self._processing_failed,
                f"Unexpected error: {exc}",
            )

    def _workflow_processing_succeeded(
        self,
        result: WorkflowResult,
    ) -> None:
        for message in workflow_result_messages(
            result
        ):
            self._append_result(
                f"{message}\n"
            )

        self.progress.stop()
        self._set_buttons_state(tk.NORMAL)

        self.status_var.set(
            f"Done: {result.successful_count} succeeded, "
            f"{result.skipped_count} skipped, "
            f"{result.failed_count} failed"
        )

        completion_message = (
            f"Successful: {result.successful_count}\n"
            f"Skipped: {result.skipped_count}\n"
            f"Failed: {result.failed_count}\n"
            f"Transactions: {result.transaction_count}\n"
            f"Yearly CSVs: {len(result.yearly_outputs)}"
        )

        if result.failed_count:
            messagebox.showwarning(
                "Workflow Complete with Errors",
                completion_message,
            )
        else:
            messagebox.showinfo(
                "Workflow Complete",
                completion_message,
            )

    def _collection_succeeded(
        self,
        result: InstitutionCollectionResult,
    ) -> None:
        for message in institution_collection_result_messages(
            result
        ):
            self._append_result(
                f"{message}\n"
            )

        self.progress.stop()
        self._set_buttons_state(tk.NORMAL)

        self.status_var.set(
            "Done: collected "
            f"{result.collected_count} normalized statement CSVs"
        )

        completion_message = (
            f"Institution: {result.institution}\n"
            "Normalized statement CSVs collected: "
            f"{result.collected_count}\n"
            "Combined transactions: "
            f"{result.combined_transaction_count}\n"
            f"Combined CSV: {result.combined_csv_path}\n"
            "Stale managed files removed: "
            f"{result.stale_removed_count}\n"
            "Source: configured output folders for all profiles "
            "at this institution\n"
            f"Collection folder: {result.all_statements_folder}"
        )

        messagebox.showinfo(
            "Institution Collection Complete",
            completion_message,
        )

    def _processing_failed(self, error_message: str) -> None:
        self._append_result(f"\nError: {error_message}\n")
        self.status_var.set("Processing failed")
        self.progress.stop()
        self._set_buttons_state(tk.NORMAL)
        messagebox.showerror("Error", error_message)

    def _set_buttons_state(self, state: str) -> None:
        self.process_btn.config(state=state)
        self.collect_btn.config(state=state)

    def _append_result(self, text: str) -> None:
        self.results_text.insert(tk.END, text)
        self.results_text.see(tk.END)


def main() -> None:
    root = tk.Tk()
    try:
        PDFBillExtractorApp(root)
    except (
        PDFProcessingError,
        ProfileError,
        WorkflowError,
        InstitutionCollectionError,
    ) as exc:
        logger.error("Application startup failed: %s", exc)
        messagebox.showerror("Startup Error", str(exc))
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    main()
