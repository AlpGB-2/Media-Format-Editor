import os
import sys
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image
import json
import logging
from logging.handlers import RotatingFileHandler
import time
import traceback
import platform

def get_resource_path(relative_path):
    """Determine file path for PyInstaller packaging and development mode."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def setup_logger():
    """Setup comprehensive logging system"""
    # Create logs directory
    log_dir = os.path.expanduser("~/.format_editor_logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "format_editor.log")
    
    # Create logger
    logger = logging.getLogger("FormatEditor")
    logger.setLevel(logging.DEBUG)
    
    # Clear existing handlers to prevent duplicates
    logger.handlers.clear()
    
    # File handler with rotation (max 5MB per file, keep 5 files)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5MB
        backupCount=5
    )
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    
    # Formatter with timestamp
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(threadName)-12s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger, log_file

# Initialize logger
logger, log_file = setup_logger()

def log_system_info():
    """Log system information at startup"""
    logger.info("=" * 80)
    logger.info("FORMAT EDITOR STARTUP")
    logger.info("=" * 80)
    logger.info(f"Python Version: {sys.version}")
    logger.info(f"Platform: {platform.platform()}")
    logger.info(f"Machine: {platform.machine()}")
    logger.info(f"Processor: {platform.processor()}")
    logger.info(f"Log File: {log_file}")
    logger.info("=" * 80)

class FormatEditor(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        log_system_info()
        logger.info("Initializing FormatEditor application...")

        # FFmpeg path configuration for macOS with bundled binary
        self.ffmpeg_path = get_resource_path(os.path.join("resources", "ffmpeg"))
        logger.debug(f"FFmpeg path: {self.ffmpeg_path}")
        
        # Make FFmpeg executable
        try:
            os.chmod(self.ffmpeg_path, 0o755)
            logger.debug("FFmpeg permissions set to executable")
        except Exception as e:
            logger.warning(f"Could not set FFmpeg permissions: {e}")
        
        # Verify FFmpeg exists
        if not os.path.exists(self.ffmpeg_path):
            logger.error(f"FFmpeg binary not found at: {self.ffmpeg_path}")
            messagebox.showerror(
                "FFmpeg Not Found",
                f"FFmpeg binary not found at:\n{self.ffmpeg_path}\n\n"
                "Make sure resources/ffmpeg exists in the app."
            )
            sys.exit(1)
        
        logger.info(f"FFmpeg binary found and verified")
        
        # Test FFmpeg
        try:
            result = subprocess.run(
                [self.ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            ffmpeg_version = result.stdout.split('\n')[0]
            logger.info(f"FFmpeg version: {ffmpeg_version}")
        except Exception as e:
            logger.warning(f"Could not verify FFmpeg version: {e}")

        self.title("Format Editor")
        self.geometry("1300x950")
        ctk.set_appearance_mode("dark")
        
        # State Variables
        self.current_files = []
        self.output_folder = os.path.expanduser("~")
        self.completed_tasks = 0
        self.total_tasks = 0
        self.current_file = ""
        self.start_time = None
        self.file_times = []  # Track time per file for ETA
        self.concurrent_workers = 1  # Number of threads to use
        self.ffmpeg_processes = []  # Track all running FFmpeg processes
        self.is_closing = False  # Flag to indicate app is closing
        
        # MB-based progress tracking
        self.total_mb = 0  # Total size of all files in MB
        self.processed_mb = 0  # Total MB processed so far
        self.current_file_size = 0  # Size of current file being processed
        self.current_file_processed = 0  # Bytes processed in current file
        self.last_update_time = None  # For speed calculation
        self.speed_history = []  # Track speed for averaging

        self.setup_ui()
        
        # Handle window close event
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Handle app closing and kill all FFmpeg processes"""
        logger.info("User closing application...")
        self.is_closing = True
        
        # Kill all running FFmpeg processes
        if self.ffmpeg_processes:
            logger.warning(f"Terminating {len(self.ffmpeg_processes)} running FFmpeg process(es)...")
            for i, proc in enumerate(self.ffmpeg_processes, 1):
                try:
                    if proc.poll() is None:  # Process is still running
                        logger.info(f"Killing FFmpeg process {i}/{len(self.ffmpeg_processes)}")
                        proc.terminate()  # Try graceful shutdown first
                        
                        # Wait a bit for graceful shutdown
                        try:
                            proc.wait(timeout=2)
                            logger.debug(f"FFmpeg process {i} terminated gracefully")
                        except subprocess.TimeoutExpired:
                            # Force kill if graceful shutdown fails
                            logger.warning(f"FFmpeg process {i} not responding, force killing...")
                            proc.kill()
                            proc.wait()
                            logger.debug(f"FFmpeg process {i} force killed")
                except Exception as e:
                    logger.error(f"Error killing FFmpeg process {i}: {e}")
            
            logger.info("All FFmpeg processes terminated")
        
        logger.info("Closing application...")
        self.destroy()

    def setup_ui(self):
        """Setup main UI"""
        # Header
        header_frame = ctk.CTkFrame(self, fg_color="transparent")
        header_frame.pack(pady=10, padx=20, fill="x")
        
        ctk.CTkLabel(
            header_frame, 
            text="Format Editor", 
            font=("Helvetica", 24, "bold")
        ).pack()

        # Main content area
        content_frame = ctk.CTkFrame(self)
        content_frame.pack(pady=10, padx=20, fill="both", expand=True)

        # --- LEFT SIDE: File List ---
        left_frame = ctk.CTkFrame(content_frame)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ctk.CTkLabel(
            left_frame,
            text="Files to Convert",
            font=("Helvetica", 13, "bold")
        ).pack(anchor="w", pady=(0, 8))

        # File listbox with scrollbar
        listbox_frame = ctk.CTkFrame(left_frame)
        listbox_frame.pack(fill="both", expand=True)

        self.file_list = ctk.CTkTextbox(
            listbox_frame,
            fg_color="#1e293b",
            text_color="#e2e8f0",
            border_width=1,
            border_color="#334155",
            height=120
        )
        self.file_list.pack(fill="both", expand=True, side="left")

        # File management buttons
        button_frame = ctk.CTkFrame(left_frame, fg_color="transparent")
        button_frame.pack(fill="x", pady=(5, 0))

        ctk.CTkButton(
            button_frame,
            text="+ Add Files",
            command=self.add_files,
            height=35,
            fg_color="#3b82f6",
            hover_color="#2563eb"
        ).pack(side="left", padx=(0, 5))

        ctk.CTkButton(
            button_frame,
            text="Clear All",
            command=self.clear_files,
            height=35,
            fg_color="#ef4444",
            hover_color="#dc2626"
        ).pack(side="left")

        # --- RIGHT SIDE: Settings ---
        right_frame = ctk.CTkFrame(content_frame)
        right_frame.pack(side="right", fill="both", padx=(10, 0), ipadx=8)

        # File type selection
        ctk.CTkLabel(
            right_frame,
            text="File Type",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w", pady=(0, 3))

        self.file_type_var = ctk.StringVar(value="Video")
        file_type_menu = ctk.CTkOptionMenu(
            right_frame,
            values=["Video", "Photo", "Audio"],
            variable=self.file_type_var,
            command=self.update_formats,
            height=35,
            fg_color="#1e293b",
            button_color="#3b82f6",
            button_hover_color="#2563eb"
        )
        file_type_menu.pack(fill="x", pady=(0, 10))

        # Output format selection
        ctk.CTkLabel(
            right_frame,
            text="Output Format",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w", pady=(0, 3))

        self.format_var = ctk.StringVar(value="MP4")
        self.format_menu = ctk.CTkOptionMenu(
            right_frame,
            values=["MP4", "MKV", "MOV", "WEBM", "AVI", "GIF"],
            variable=self.format_var,
            height=35,
            fg_color="#1e293b",
            button_color="#3b82f6",
            button_hover_color="#2563eb"
        )
        self.format_menu.pack(fill="x", pady=(0, 10))

        # Quality/Bitrate settings
        ctk.CTkLabel(
            right_frame,
            text="Quality",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w", pady=(0, 3))

        self.quality_var = ctk.StringVar(value="High")
        quality_menu = ctk.CTkOptionMenu(
            right_frame,
            values=["Low", "Medium", "High", "Very High"],
            variable=self.quality_var,
            height=35,
            fg_color="#1e293b",
            button_color="#3b82f6",
            button_hover_color="#2563eb"
        )
        quality_menu.pack(fill="x", pady=(0, 12))

        # Concurrent Workers (Threading)
        ctk.CTkLabel(
            right_frame,
            text="Concurrent Files",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w", pady=(0, 3))

        self.workers_var = ctk.StringVar(value="1")
        workers_menu = ctk.CTkOptionMenu(
            right_frame,
            values=["1", "2", "4", "6"],
            variable=self.workers_var,
            height=35,
            fg_color="#1e293b",
            button_color="#3b82f6",
            button_hover_color="#2563eb"
        )
        workers_menu.pack(fill="x", pady=(0, 12))

        # Output folder selection
        ctk.CTkLabel(
            right_frame,
            text="Output Folder",
            font=("Helvetica", 12, "bold")
        ).pack(anchor="w", pady=(0, 3))

        output_btn_frame = ctk.CTkFrame(right_frame, fg_color="transparent")
        output_btn_frame.pack(fill="x", pady=(0, 8))

        ctk.CTkButton(
            output_btn_frame,
            text="📁 Browse",
            command=self.select_output_folder,
            height=35,
            width=80,
            fg_color="#8b5cf6",
            hover_color="#7c3aed"
        ).pack(side="left", padx=(0, 5))

        self.output_label = ctk.CTkLabel(
            right_frame,
            text="Default (Home)",
            font=("Helvetica", 10),
            text_color="#94a3b8"
        )
        self.output_label.pack(anchor="w", pady=(0, 10))

        # Info box
        info_frame = ctk.CTkFrame(right_frame, fg_color="#1e293b", border_width=1, border_color="#334155")
        info_frame.pack(fill="x", pady=(8, 0))

        ctk.CTkLabel(
            info_frame,
            text="Info",
            font=("Helvetica", 11, "bold"),
            text_color="#10b981"
        ).pack(anchor="w", padx=10, pady=(6, 2))

        self.info_label = ctk.CTkLabel(
            info_frame,
            text="No files selected",
            font=("Helvetica", 10),
            text_color="#cbd5e1",
            justify="left"
        )
        self.info_label.pack(anchor="w", padx=10, pady=(0, 6))

        # Progress section with detailed stats
        progress_frame = ctk.CTkFrame(self, fg_color="transparent")
        progress_frame.pack(pady=8, padx=20, fill="x")

        self.progress_label = ctk.CTkLabel(
            progress_frame,
            text="Ready",
            font=("Helvetica", 11),
            text_color="#cbd5e1"
        )
        self.progress_label.pack(anchor="w", pady=(0, 3))

        self.progress_bar = ctk.CTkProgressBar(
            progress_frame,
            height=8,
            fg_color="#334155",
            progress_color="#3b82f6"
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", pady=(0, 6))

        # Stats display area
        stats_frame = ctk.CTkFrame(progress_frame, fg_color="#1e293b", border_width=1, border_color="#334155")
        stats_frame.pack(fill="x", pady=(0, 0), padx=0)

        stats_header = ctk.CTkLabel(
            stats_frame,
            text="Progress Details",
            font=("Helvetica", 10, "bold"),
            text_color="#94a3b8"
        )
        stats_header.pack(anchor="w", padx=10, pady=(4, 2))

        # Stats content frame
        stats_content = ctk.CTkFrame(stats_frame, fg_color="transparent")
        stats_content.pack(fill="x", padx=10, pady=(0, 4))

        # Left column: Current file and percentage
        left_stats = ctk.CTkFrame(stats_content, fg_color="transparent")
        left_stats.pack(side="left", fill="both", expand=True, padx=(0, 10))

        ctk.CTkLabel(
            left_stats,
            text="Processing:",
            font=("Helvetica", 9, "bold"),
            text_color="#cbd5e1"
        ).pack(anchor="w")

        self.current_file_label = ctk.CTkLabel(
            left_stats,
            text="None",
            font=("Helvetica", 9),
            text_color="#94a3b8"
        )
        self.current_file_label.pack(anchor="w", padx=(0, 0))

        # Right column: Stats (percentage, time, count)
        right_stats = ctk.CTkFrame(stats_content, fg_color="transparent")
        right_stats.pack(side="right", fill="both", expand=True)

        self.percentage_label = ctk.CTkLabel(
            right_stats,
            text="0%",
            font=("Helvetica", 9, "bold"),
            text_color="#10b981"
        )
        self.percentage_label.pack(anchor="e", pady=(0, 1))

        self.eta_label = ctk.CTkLabel(
            right_stats,
            text="ETA: --:--",
            font=("Helvetica", 9),
            text_color="#cbd5e1"
        )
        self.eta_label.pack(anchor="e", pady=(0, 1))

        self.count_label = ctk.CTkLabel(
            right_stats,
            text="0/0 MB",
            font=("Helvetica", 9),
            text_color="#cbd5e1"
        )
        self.count_label.pack(anchor="e")

        # Start button
        self.start_btn = ctk.CTkButton(
            self,
            text="▶ START CONVERSION",
            command=self.start_conversion,
            height=45,
            font=("Helvetica", 14, "bold"),
            fg_color="#10b981",
            hover_color="#059669"
        )
        self.start_btn.pack(pady=(0, 20), padx=20, fill="x")

    def update_formats(self, value):
        """Update available formats based on file type"""
        formats = {
            "Video": ["MP4", "MKV", "MOV", "WEBM", "AVI", "GIF"],
            "Photo": ["PNG", "JPG", "WEBP", "BMP", "ICO"],
            "Audio": ["MP3", "WAV", "AAC", "FLAC", "OGG"]
        }
        self.format_menu.configure(values=formats[value])
        self.format_menu.set(formats[value][0])

    def add_files(self):
        """Add files to conversion list"""
        logger.info("User clicked 'Add Files'")
        files = filedialog.askopenfilenames(
            title="Select Files to Convert",
            filetypes=[("All Files", "*.*")]
        )
        if files:
            logger.info(f"User selected {len(files)} file(s)")
            for f in files:
                if f not in self.current_files:
                    self.current_files.append(f)
                    logger.debug(f"Added file: {f} (size: {os.path.getsize(f)} bytes)")
                else:
                    logger.debug(f"File already in list, skipped: {f}")
            self.refresh_file_list()
            logger.info(f"Total files in queue: {len(self.current_files)}")
        else:
            logger.debug("User cancelled file selection")

    def clear_files(self):
        """Clear all files"""
        logger.info(f"User cleared file queue ({len(self.current_files)} files removed)")
        self.current_files.clear()
        self.refresh_file_list()

    def refresh_file_list(self):
        """Refresh file list display"""
        self.file_list.delete("0.0", "end")
        if self.current_files:
            display_text = "\n".join(
                [f"{i+1}. {os.path.basename(f)}" for i, f in enumerate(self.current_files)]
            )
            self.file_list.insert("0.0", display_text)
            self.info_label.configure(
                text=f"Files: {len(self.current_files)}\nTotal size: {self.get_total_size()}"
            )
        else:
            self.file_list.insert("0.0", "No files selected")
            self.info_label.configure(text="No files selected")

    def get_total_size(self):
        """Calculate total size of selected files"""
        try:
            total = sum(os.path.getsize(f) for f in self.current_files)
            return self.format_size(total)
        except:
            return "Unknown"

    def format_size(self, bytes):
        """Format bytes to human readable format"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if bytes < 1024:
                return f"{bytes:.1f} {unit}"
            bytes /= 1024
        return f"{bytes:.1f} TB"

    def select_output_folder(self):
        """Select output folder"""
        folder = filedialog.askdirectory(title="Select Output Folder")
        if folder:
            self.output_folder = folder
            logger.info(f"Output folder changed: {folder}")
            folder_name = os.path.basename(folder) if folder != os.path.expanduser("~") else "Home"
            self.output_label.configure(text=folder_name)
        else:
            logger.debug("User cancelled output folder selection")

    def start_conversion(self):
        """Start file conversion"""
        logger.info("User clicked 'START CONVERSION'")
        if not self.current_files:
            logger.warning("Start conversion clicked but no files in queue")
            messagebox.showwarning("No Files", "Please add files first!")
            return

        self.concurrent_workers = int(self.workers_var.get())
        logger.info("=" * 80)
        logger.info("CONVERSION SESSION STARTED")
        logger.info("=" * 80)
        logger.info(f"Files to process: {len(self.current_files)}")
        logger.info(f"Concurrent workers: {self.concurrent_workers}")
        logger.info(f"Target format: {self.format_var.get()}")
        logger.info(f"Quality preset: {self.quality_var.get()}")
        logger.info(f"Output folder: {self.output_folder}")
        
        for i, f in enumerate(self.current_files, 1):
            logger.debug(f"  {i}. {os.path.basename(f)} ({os.path.getsize(f)} bytes)")
        
        self.start_btn.configure(state="disabled", text="⏳ CONVERTING...")
        self.completed_tasks = 0
        self.total_tasks = len(self.current_files)
        self.file_times = []
        self.start_time = __import__('time').time()
        self.progress_bar.set(0)
        
        # Calculate total MB
        self.total_mb = sum(os.path.getsize(f) for f in self.current_files) / (1024 * 1024)
        self.processed_mb = 0
        self.speed_history = []
        self.last_update_time = self.start_time

        threading.Thread(target=self.run_conversion, daemon=True).start()

    def run_conversion(self):
        """Run conversion in background with thread pool"""
        import time
        target_format = self.format_var.get().lower()
        
        logger.info(f"Starting thread pool with {self.concurrent_workers} workers...")
        logger.info(f"Total size to process: {self.total_mb:.2f} MB")
        
        with ThreadPoolExecutor(max_workers=self.concurrent_workers) as executor:
            futures = []
            for file_path in self.current_files:
                future = executor.submit(self.convert_file, file_path, target_format)
                futures.append(future)
            
            logger.info(f"Submitted {len(futures)} conversion tasks")
            
            # Wait for all to complete
            for future in futures:
                future.result()

        logger.info("All conversion tasks completed")
        self.after(0, self.finish_conversion)

    def convert_file(self, input_path, target_format):
        """Convert individual file"""
        import time
        file_name = os.path.basename(input_path)
        thread_name = threading.current_thread().name
        
        # Check if app is closing
        if self.is_closing:
            logger.warning(f"[{thread_name}] Skipping conversion of {file_name} - app is closing")
            return
        
        logger.info(f"[{thread_name}] Starting conversion: {file_name}")
        
        try:
            # Update current file
            self.current_file = file_name
            self.current_file_size = os.path.getsize(input_path)
            self.current_file_processed = 0
            self.after(0, self.update_progress)

            file_start = time.time()

            output_path = os.path.join(
                self.output_folder,
                f"{os.path.splitext(os.path.basename(input_path))[0]}_converted.{target_format}"
            )
            
            logger.debug(f"[{thread_name}] Input: {input_path}")
            logger.debug(f"[{thread_name}] Output: {output_path}")

            # Get quality settings
            quality_presets = {
                "Low": ["-crf", "28"],
                "Medium": ["-crf", "23"],
                "High": ["-crf", "18"],
                "Very High": ["-crf", "12"]
            }
            quality_args = quality_presets.get(self.quality_var.get(), ["-crf", "23"])

            cmd = [
                self.ffmpeg_path,
                "-i", input_path,
                "-y",
                "-hide_banner",
                "-progress", "pipe:1",
                *quality_args,
                output_path
            ]
            
            logger.debug(f"[{thread_name}] FFmpeg command: {' '.join(cmd)}")

            # Start process and track it
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Add to tracking list
            self.ffmpeg_processes.append(proc)
            logger.debug(f"[{thread_name}] FFmpeg process started (PID: {proc.pid})")
            
            # Monitor progress in real-time
            if proc.stdout:
                for line in iter(proc.stdout.readline, ''):
                    if self.is_closing:
                        proc.terminate()
                        break
                    
                    if line.startswith('out_time_ms='):
                        try:
                            # Get bytes processed (output file size so far)
                            if os.path.exists(output_path):
                                current_size = os.path.getsize(output_path)
                                self.current_file_processed = current_size
                                self.after(0, self.update_progress)
                        except:
                            pass
            
            # Wait for process to finish
            proc.wait()
            
            # Remove from tracking list
            if proc in self.ffmpeg_processes:
                self.ffmpeg_processes.remove(proc)
            
            # Track time taken
            file_time = time.time() - file_start
            self.file_times.append(file_time)
            
            # Add completed file size to processed
            if proc.returncode == 0:
                file_size_mb = self.current_file_size / (1024 * 1024)
                self.processed_mb += file_size_mb
                output_size = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                logger.info(f"[{thread_name}] ✓ Conversion successful: {file_name} ({file_time:.2f}s) → {output_size} bytes")
            else:
                stderr = proc.stderr.read() if proc.stderr else ""
                logger.error(f"[{thread_name}] ✗ Conversion failed: {file_name}")
                logger.error(f"[{thread_name}] FFmpeg stderr: {stderr}")

        except Exception as e:
            logger.error(f"[{thread_name}] Exception during conversion of {file_name}: {e}")
            logger.error(f"[{thread_name}] Traceback: {traceback.format_exc()}")
        finally:
            self.completed_tasks += 1
            self.after(0, self.update_progress)

    def update_progress(self):
        """Update progress bar and detailed stats based on MB processed"""
        import time
        
        if self.total_mb > 0:
            # Progress based on MB processed
            progress = min(self.processed_mb / self.total_mb, 1.0)
            self.progress_bar.set(progress)
            
            # Percentage
            percentage = int(progress * 100)
            self.percentage_label.configure(text=f"{percentage}%")
            
            # Current file
            if self.current_file:
                display_name = self.current_file
                if len(display_name) > 35:
                    display_name = "..." + display_name[-32:]
                self.current_file_label.configure(text=display_name)
            
            # MB processed count
            self.count_label.configure(text=f"{self.processed_mb:.1f}/{self.total_mb:.1f} MB")
            
            # Calculate ETA based on speed
            if self.start_time:
                elapsed = time.time() - self.start_time
                if elapsed > 0 and self.processed_mb > 0:
                    speed_mbps = self.processed_mb / elapsed  # MB per second
                    remaining_mb = max(0, self.total_mb - self.processed_mb)
                    
                    if speed_mbps > 0:
                        estimated_seconds = remaining_mb / speed_mbps
                        
                        # Store speed for averaging
                        if len(self.speed_history) >= 10:
                            self.speed_history.pop(0)
                        self.speed_history.append(speed_mbps)
                        
                        # Use average speed for better ETA
                        avg_speed = sum(self.speed_history) / len(self.speed_history)
                        estimated_seconds = remaining_mb / avg_speed if avg_speed > 0 else estimated_seconds
                        
                        mins, secs = divmod(int(estimated_seconds), 60)
                        self.eta_label.configure(text=f"ETA: {mins}:{secs:02d} ({avg_speed:.2f} MB/s)")
                    else:
                        self.eta_label.configure(text="ETA: --:--")
                else:
                    self.eta_label.configure(text="ETA: --:--")
            else:
                self.eta_label.configure(text="ETA: --:--")
            
            self.progress_label.configure(
                text=f"Processing: {self.completed_tasks} / {self.total_tasks} files"
            )

    def finish_conversion(self):
        """Handle conversion completion"""
        logger.info("=" * 80)
        logger.info("CONVERSION SESSION COMPLETED")
        logger.info("=" * 80)
        
        # Calculate stats
        total_time = time.time() - self.start_time if self.start_time else 0
        successful = self.total_tasks
        
        logger.info(f"Total files processed: {successful}/{self.total_tasks}")
        logger.info(f"Total data processed: {self.total_mb:.2f} MB")
        logger.info(f"Total time elapsed: {total_time:.2f} seconds ({total_time/60:.2f} minutes)")
        
        if total_time > 0:
            throughput_mbps = self.total_mb / total_time
            logger.info(f"Average speed: {throughput_mbps:.2f} MB/s")
        
        if self.file_times:
            avg_time = sum(self.file_times) / len(self.file_times)
            logger.info(f"Average time per file: {avg_time:.2f} seconds")
            logger.info(f"Fastest file: {min(self.file_times):.2f} seconds")
            logger.info(f"Slowest file: {max(self.file_times):.2f} seconds")
        
        # Clean up any remaining processes
        self.ffmpeg_processes.clear()
        logger.debug("FFmpeg process tracking cleared")
        logger.info("=" * 80)
        
        self.start_btn.configure(state="normal", text="▶ START CONVERSION")
        self.progress_bar.set(1)
        self.progress_label.configure(text="✓ Conversion complete!")
        
        messagebox.showinfo(
            "Complete",
            f"Successfully converted {self.total_tasks} file(s)!\n\nOutput folder: {self.output_folder}"
        )
        
        # Reset UI
        self.progress_bar.set(0)
        self.progress_label.configure(text="Ready")
        self.current_file_label.configure(text="None")
        self.percentage_label.configure(text="0%")
        self.eta_label.configure(text="ETA: --:--")
        self.count_label.configure(text="0/0 MB")
        self.current_file = ""

if __name__ == "__main__":
    logger.info("Starting Format Editor application...")
    try:
        app = FormatEditor()
        logger.info("Application window created successfully")
        app.mainloop()
    except Exception as e:
        logger.critical(f"Fatal error in application: {e}")
        logger.critical(f"Traceback: {traceback.format_exc()}")
        raise
    finally:
        logger.info("Format Editor application closed")
        logger.info("=" * 80)