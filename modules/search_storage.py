import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .browser_config import load_config


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SEARCH_DIR = PROJECT_ROOT / "data" / "searches"


class SearchStorage:
    def __init__(self, directory=None, default_limit=None):
        config = load_config() if directory is None or default_limit is None else None
        self.default_limit = default_limit if default_limit is not None else config.default_limit
        self.directory = Path(directory if directory is not None else config.directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def result_path(self, name):
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not name.endswith(".json")
            or name.endswith(".meta.json")
        ):
            raise ValueError("Invalid result filename.")
        path = self.directory / name
        if path.is_symlink():
            raise ValueError("Result files must be stored inside the search folder.")
        return path

    def read(self, name):
        with self.result_path(name).open(encoding="utf-8") as file:
            papers = json.load(file)
        if not isinstance(papers, list) or any(
            not isinstance(paper, dict)
            or not isinstance(paper.get("authors"), list)
            or not isinstance(paper.get("title"), str)
            for paper in papers
        ):
            raise ValueError("Expected a JSON array of papers with title and authors fields.")
        return papers

    def metadata(self, name):
        path = self.result_path(name).with_suffix(".meta.json")
        if path.is_file() and not path.is_symlink():
            with path.open(encoding="utf-8") as file:
                return json.load(file)
        return {}

    def save(self, papers, metadata):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = f"search_{stamp}_{uuid4().hex[:10]}.json"
        path = self.result_path(name)
        with self.lock:
            with path.open("x", encoding="utf-8") as file:
                json.dump(papers, file, ensure_ascii=False, indent=2)
                file.write("\n")
            with path.with_suffix(".meta.json").open("x", encoding="utf-8") as file:
                json.dump(metadata, file, ensure_ascii=False, indent=2)
        return name

    def delete_many(self, names):
        if not isinstance(names, list) or not names or len(names) > 500:
            raise ValueError("Select between 1 and 500 saved searches.")
        if any(not isinstance(name, str) for name in names):
            raise ValueError("Result filenames must be strings.")
        names = list(dict.fromkeys(names))
        with self.lock:
            plan = []
            for name in names:
                result = self.result_path(name)
                if not result.is_file():
                    raise FileNotFoundError(name)
                files = [result]
                meta = result.with_suffix(".meta.json")
                if meta.is_symlink():
                    raise ValueError("Metadata files cannot be symbolic links.")
                if meta.exists():
                    if not meta.is_file():
                        raise ValueError("Expected a metadata file.")
                    files.append(meta)
                plan.append((name, files))
            deleted, errors = [], []
            for name, files in plan:
                try:
                    files[0].unlink()
                except OSError as error:
                    errors.append({"filename": name, "error": str(error)})
                    continue
                deleted.append(name)
                for meta in files[1:]:
                    try:
                        meta.unlink()
                    except OSError as error:
                        errors.append({"filename": meta.name, "error": str(error)})
        return {"deleted": deleted, "errors": errors}

    def history(self, text=""):
        records = []
        with self.lock:
            paths = sorted(
                self.directory.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
            )
            for path in paths:
                if path.name.endswith(".meta.json") or path.is_symlink():
                    continue
                try:
                    papers = self.read(path.name)
                    meta = self.metadata(path.name)
                    record = {
                        "filename": path.name,
                        "count": len(papers),
                        "created_at": meta.get(
                            "created_at",
                            datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                        ),
                        "status": meta.get("status", "imported"),
                        "error": meta.get("error"),
                        "elapsed": meta.get("elapsed", 0),
                        "events": meta.get("events", []),
                        "total": meta.get("total", len(papers)),
                        "keywords": meta.get("keywords", ""),
                        "expression": meta.get("expression", ""),
                        "field": meta.get("field", "all"),
                        "query": meta.get("query", ""),
                        "limit": meta.get("limit", len(papers) or self.default_limit),
                        "mode": meta.get("mode", "count"),
                        "recent_days": meta.get("recent_days"),
                        "date_start": meta.get("date_start"),
                        "date_end": meta.get("date_end"),
                        "include_abstract": meta.get(
                            "include_abstract", any("summary" in paper for paper in papers)
                        ),
                    }
                    if (
                        text.casefold()
                        in " ".join(str(value) for value in record.values()).casefold()
                    ):
                        records.append(record)
                except (OSError, ValueError, TypeError):
                    continue
        return records
