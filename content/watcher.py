"""Google Drive folder watcher for OpenClaw content pipeline.

Monitors a local folder (synced via Google Drive for Desktop) for new video
files and triggers the pipeline when one appears.

Usage:
    from content.watcher import start_watching
    start_watching(callback=pipeline.process)
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Set

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class _VideoHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None]) -> None:
        super().__init__()
        self._callback = callback
        self._seen: Set[str] = set()

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        if str(path) in self._seen:
            return
        self._seen.add(str(path))
        # Brief wait to ensure the file has fully synced before processing
        time.sleep(2)
        self._callback(path)


def start_watching(
    callback: Callable[[Path], None],
    folder: str | None = None,
    blocking: bool = True,
) -> Observer:
    """Start watching the Google Drive folder for new videos.

    Args:
        callback: Called with the Path of each new video file.
        folder: Override the watch folder (defaults to GDRIVE_WATCH_FOLDER env var).
        blocking: If True, blocks until KeyboardInterrupt. Set False for testing.

    Returns:
        The running Observer (useful when blocking=False).

    Raises:
        ValueError: If no watch folder is configured.
    """
    watch_dir = folder or os.getenv("GDRIVE_WATCH_FOLDER", "").strip()
    if not watch_dir:
        raise ValueError("GDRIVE_WATCH_FOLDER is not set. Add it to your .env file.")

    watch_path = Path(watch_dir)
    watch_path.mkdir(parents=True, exist_ok=True)

    handler = _VideoHandler(callback)
    observer = Observer()
    observer.schedule(handler, str(watch_path), recursive=False)
    observer.start()

    print(f"👁  Watching for new videos in: {watch_path}")

    if blocking:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            observer.stop()
        observer.join()

    return observer
