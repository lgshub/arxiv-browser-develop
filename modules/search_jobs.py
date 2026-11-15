import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import arxiv
import requests

from .paper_metadata import paper_to_record
from .browser_config import load_config


_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST = 0.0


class SearchCancelled(Exception):
    pass


class TimedSession(requests.Session):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def get(self, url, **kwargs):
        global _LAST_REQUEST
        kwargs.setdefault(
            "timeout", (self.config.connect_timeout_seconds, self.config.read_timeout_seconds)
        )
        with _REQUEST_LOCK:
            time.sleep(
                max(0, self.config.request_interval_seconds - (time.monotonic() - _LAST_REQUEST))
            )
            _LAST_REQUEST = time.monotonic()
            return super().get(url, **kwargs)


class ProgressClient(arxiv.Client):
    def __init__(self, job):
        super().__init__(
            page_size=min(job.limit, job.config.api_page_size)
            if job.limit is not None
            else job.config.api_page_size,
            delay_seconds=job.config.request_interval_seconds,
            num_retries=0,
        )
        self._session.close()
        self._session = TimedSession(job.config)
        self.job = job

    def _parse_feed(self, url, first_page=True, _try_index=0):
        start = int(parse_qs(urlparse(url).query)["start"][0])
        attempts = self.job.config.retries + 1
        for attempt in range(attempts):
            self.job.check_cancelled()
            end = start + self.page_size
            if self.job.limit is not None:
                end = min(end, self.job.limit)
            self.job.event(
                f"Requesting papers {start + 1}–{end} · attempt {attempt + 1}/{attempts}"
            )
            try:
                feed = super()._parse_feed(url, first_page=first_page)
                if feed.malformed:
                    raise ValueError("arXiv returned malformed XML. Try again later.")
                for paper in feed.results:
                    if "arxiv.org/api/errors" in paper.entry_id:
                        raise ValueError(f"arXiv rejected this query: {paper.summary}")
                if self.job.limit is None and feed.header.total_results > 300000:
                    raise ValueError(
                        "This date window exceeds arXiv's 300,000-result limit. Shorten the window or refine the query."
                    )
                with self.job.lock:
                    self.job.total = feed.header.total_results
                    self.job.target = (
                        min(self.job.limit, self.job.total)
                        if self.job.limit is not None
                        else self.job.total
                    )
                self.job.event(
                    f"Received {len(feed.results)} papers · {feed.header.total_results:,} total matches"
                )
                return feed
            except (arxiv.ArxivError, requests.RequestException) as error:
                self.job.event(f"Request failed: {type(error).__name__}: {error}")
                if attempt == attempts - 1:
                    raise
                self.job.event("Retrying after the request interval…")
                if self.job.cancel.wait(self.job.config.request_interval_seconds):
                    raise SearchCancelled()


class SearchJob:
    def __init__(self, options, config=None):
        self.config = config or load_config()
        self.id = uuid4().hex
        self.options = options
        self.limit = options["limit"]
        self.target = self.limit
        self.total = None
        self.papers = []
        self.seen = set()
        self.status = "queued"
        self.error = None
        self.filename = None
        self.started = time.monotonic()
        self.finished = None
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.events = []
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.event("Queued for search")

    def event(self, message):
        with self.lock:
            self.events.append(
                {"time": datetime.now(timezone.utc).strftime("%H:%M:%S"), "message": message}
            )
            self.events = self.events[-200:]

    def check_cancelled(self):
        if self.cancel.is_set():
            raise SearchCancelled()

    def snapshot(self):
        with self.lock:
            return {
                "id": self.id,
                "status": self.status,
                "fetched": len(self.papers),
                "limit": self.limit,
                "target": self.target,
                "total": self.total,
                "error": self.error,
                "filename": self.filename,
                "elapsed": round((self.finished or time.monotonic()) - self.started),
                "events": list(self.events),
                "query": self.options["query"],
                "mode": self.options.get("mode", "count"),
                "recent_days": self.options.get("recent_days"),
                "date_start": self.options.get("date_start"),
                "date_end": self.options.get("date_end"),
            }

    def run(self, storage, client_factory=ProgressClient):
        client = None
        try:
            self.check_cancelled()
            with self.lock:
                self.status = "running"
            client = client_factory(self)
            search = arxiv.Search(
                query=self.options["query"],
                max_results=self.limit,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )
            for paper in client.results(search):
                self.check_cancelled()
                if paper.entry_id in self.seen:
                    continue
                record = paper_to_record(paper, self.options["include_abstract"])
                with self.lock:
                    self.papers.append(record)
                    self.seen.add(paper.entry_id)
            self.check_cancelled()
            outcome = "completed"
            self.event(f"Search complete · {len(self.papers)} unique papers")
        except SearchCancelled:
            outcome = "cancelled"
            self.event("Search cancelled; received papers will be saved")
        except Exception as error:
            outcome = "failed"
            self.error = f"{type(error).__name__}: {error}"
            self.event(self.error)
        finally:
            if client is not None:
                client._session.close()
        try:
            metadata = {
                **self.options,
                "created_at": self.created_at,
                "status": outcome,
                "error": self.error,
                "count": len(self.papers),
                "total": self.total,
                "events": list(self.events),
                "elapsed": round(time.monotonic() - self.started),
            }
            self.filename = storage.save(self.papers, metadata)
            self.event(f"Saved {len(self.papers)} papers to {self.filename}")
        except Exception as error:
            outcome = "failed"
            self.error = f"Could not save results: {error}"
            self.event(self.error)
        with self.lock:
            self.finished = time.monotonic()
            self.status = outcome


class JobManager:
    def __init__(self, storage, client_factory=ProgressClient, config=None):
        self.config = config or load_config()
        self.storage = storage
        self.client_factory = client_factory
        self.jobs = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="paper-search")

    def submit(self, options):
        with self.lock:
            active = [job for job in self.jobs.values() if job.status in ("queued", "running")]
            if len(active) >= 5:
                raise ValueError("Five searches are already queued. Wait for one to finish.")
            for key in list(self.jobs):
                if len(self.jobs) <= 20:
                    break
                if self.jobs[key].status not in ("queued", "running"):
                    del self.jobs[key]
            job = SearchJob(options, self.config)
            self.jobs[job.id] = job
            self.executor.submit(job.run, self.storage, self.client_factory)
            return job

    def get(self, identifier):
        with self.lock:
            return self.jobs.get(identifier)
