# app/utils/job_io.py
from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.utils.logger import logger

JsonDict = dict[str, Any]


@dataclass
class JobPaths:
    root: Path
    input_dir: Path
    artifacts_dir: Path
    logs_dir: Path

    def to_dict(self) -> JsonDict:
        return asdict(self) | {
            "root": str(self.root),
            "input_dir": str(self.input_dir),
            "artifacts_dir": str(self.artifacts_dir),
            "logs_dir": str(self.logs_dir),
        }


class JobIO:
    """
    Per-request filesystem workspace.

    Structure:
      data/jobs/<job_id>/
        input/
        artifacts/
          audio/
          asr/
          diarization/
          alignment/
          nlp/
          report/
        logs/
        meta.json
    """

    def __init__(self, base_dir: str | Path = "data/jobs"):
        self.base_dir = Path(base_dir)

    def init_job(self, job_id: str) -> JobPaths:
        root = self.base_dir / job_id
        input_dir = root / "input"
        artifacts_dir = root / "artifacts"
        logs_dir = root / "logs"

        # Create folder tree
        for p in [
            input_dir,
            artifacts_dir / "audio",
            artifacts_dir / "asr",
            artifacts_dir / "diarization",
            artifacts_dir / "alignment",
            artifacts_dir / "nlp",
            artifacts_dir / "report",
            logs_dir,
        ]:
            p.mkdir(parents=True, exist_ok=True)

        return JobPaths(root=root, input_dir=input_dir, artifacts_dir=artifacts_dir, logs_dir=logs_dir)

    def p(self, job: JobPaths, rel: str) -> Path:
        return job.root / rel

    def exists(self, job: JobPaths, rel: str) -> bool:
        return self.p(job, rel).exists()

    def save_json(self, job: JobPaths, rel: str, data: Any) -> Path:
        path = self.p(job, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def load_json(self, job: JobPaths, rel: str, default: Any | None = None) -> Any:
        path = self.p(job, rel)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load JSON {path}: {e}")
            return default

    def save_text(self, job: JobPaths, rel: str, text: str) -> Path:
        path = self.p(job, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text or "", encoding="utf-8")
        return path

    def load_text(self, job: JobPaths, rel: str, default: str | None = "") -> str | None:
        """Read a text artifact, or return ``default`` when it does not exist.

        ``default`` is deliberately ``str | None``: callers that want an
        absent artifact to surface as ``None`` rather than an empty string
        pass ``default=None`` (the orchestrator does this for the PDF base64,
        where ``None`` means "no report produced"). Coercing the default to
        ``str`` would silently flip that response contract from None to "".
        """
        path = self.p(job, rel)
        return path.read_text(encoding="utf-8") if path.exists() else default

    def save_bytes(self, job: JobPaths, rel: str, blob: bytes) -> Path:
        path = self.p(job, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
        return path

    def copy_in(self, job: JobPaths, src_path: str | Path, rel_dest: str) -> Path:
        src = Path(src_path)
        dest = self.p(job, rel_dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def purge_expired(self, max_age_hours: float) -> int:
        """Delete job directories older than ``max_age_hours``.

        Bounds disk growth and caps how long per-request PII (raw audio,
        transcript, PDF) lives on disk. A directory's age is taken from its
        ``meta.json`` mtime when present, else the directory's own mtime —
        ``meta.json`` is written last on both the success and hard-fail
        paths, so it is the best "job finished" marker.

        Fail-soft: a directory that can't be stat'd or removed is logged and
        skipped, never raised — retention must not break the caller.

        Args:
            max_age_hours: age threshold in hours; ``<= 0`` disables (no-op).

        Returns:
            The number of directories removed.
        """
        if max_age_hours <= 0 or not self.base_dir.exists():
            return 0

        cutoff = time.time() - max_age_hours * 3600.0
        removed = 0
        for job_dir in self.base_dir.iterdir():
            if not job_dir.is_dir():
                continue
            try:
                meta = job_dir / "meta.json"
                mtime = meta.stat().st_mtime if meta.exists() else job_dir.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    removed += 1
            except OSError as e:
                logger.warning("purge_expired: skipped %s (%s)", job_dir, e)
        if removed:
            logger.info("purge_expired: removed %d expired job dir(s)", removed)
        return removed
