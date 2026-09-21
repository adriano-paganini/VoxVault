"""One application-owned worker; pending PCM lives on disk, not in thread arguments."""

from dataclasses import dataclass
import logging
import os
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
from threading import Lock, Thread


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingJob:
    path: Path
    timestamp: int
    enrollment: bool


class ProcessingQueue:
    def __init__(self, process, finished):
        self._process = process
        self._finished = finished
        self._queue = Queue()
        self._lock = Lock()
        self._accepting = False
        self._directory = None
        self._thread = None

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("Processing worker already started")
            self._directory = TemporaryDirectory(
                prefix="voxvault-processing-", dir=os.getenv("VOXVAULT_PROCESSING_DIR", "."),
            )
            self._thread = Thread(target=self._run, name="voxvault-audio-worker")
            self._accepting = True
            self._thread.start()
        logger.info("Audio processing worker started (concurrency=1)")

    def enqueue(self, audio, timestamp, enrollment):
        with self._lock:
            if not self._accepting:
                raise RuntimeError("Processing worker is shutting down")
            path = Path(self._directory.name) / f"{timestamp}.pcm"
            try:
                path.write_bytes(audio)
                logger.info("Recording %s queued", timestamp)
                self._queue.put_nowait(ProcessingJob(path, timestamp, enrollment))
            except Exception:
                path.unlink(missing_ok=True)
                raise

    def close(self):
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            self._queue.put_nowait(None)
        # Drain accepted jobs before releasing the spool or ending the process.
        self._thread.join()
        self._directory.cleanup()
        logger.info("Audio processing worker stopped")

    def _run(self):
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._run_job(job)
            finally:
                self._queue.task_done()
                del job

    def _run_job(self, job):
        try:
            self._process(job)
        except Exception:
            # Also isolate failures in error reporting or database recovery.
            logger.exception("Recording %s worker job failed", job.timestamp)
        finally:
            try:
                job.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove recording %s spool file", job.timestamp)
            try:
                self._finished(job.timestamp)
            except Exception:
                logger.exception("Could not finalize recording %s", job.timestamp)
