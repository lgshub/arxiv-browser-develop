import itertools
import time
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from modules.paper_browser import create_app
from modules.browser_config import load_config
from modules.paper_metadata import subject_labels
from modules.query_language import Parser, QueryError, compile_query
from modules.search_jobs import ProgressClient, SearchJob
from modules.search_storage import SearchStorage
from modules.search_options import build_search_options


def evaluate(node, truth):
    if node[0] == "atom":
        return truth[node[1]]
    if node[0] == "not":
        return not evaluate(node[1], truth)
    if node[0] == "and":
        return evaluate(node[1], truth) and evaluate(node[2], truth)
    return evaluate(node[1], truth) or evaluate(node[2], truth)


@pytest.mark.parametrize(
    "expression",
    [
        "a AND (b OR NOT c)",
        "(a OR b) AND NOT (c OR d)",
        "a AND NOT (b AND NOT (c OR d))",
        "a AND (b not in title OR NOT c)",
        "a ANDNOT (b OR c)",
    ],
)
def test_nested_boolean_meaning(expression):
    original = Parser(expression).parse()
    compiled = Parser(compile_query(expression=expression)).parse()
    fields = ["all:a", "all:b", "all:c", "all:d", "ti:b"]
    for values in itertools.product([False, True], repeat=len(fields)):
        truth = dict(zip(fields, values))
        assert evaluate(original, truth) == evaluate(compiled, truth)


def test_fields_and_default_all():
    query = compile_query("robot learning", "title", "AND (x in abstract OR y in author OR z)")
    assert query == "((ti:robot AND ti:learning) AND ((abs:x OR au:y) OR all:z))"
    assert compile_query(expression='"robot learning" in abstract') == 'abs:"robot learning"'
    assert compile_query(expression="cat:cs.RO") == "cat:cs.RO"
    assert compile_query(expression="AND x") == "all:x"
    assert compile_query("x", expression="OR y") == "(all:x OR all:y)"


@pytest.mark.parametrize(
    "expression",
    [
        "(x OR y",
        "x OR",
        '"unfinished',
        "x in unknown",
        "NOT x",
        "x OR NOT y",
        "a AND NOT a",
        "x AND ()",
        "ANDNOT y",
        "x AND",
        "(" * 31 + "x" + ")" * 31,
    ],
)
def test_invalid_expressions(expression):
    with pytest.raises(QueryError):
        compile_query(expression=expression)


def paper(index, authors=1):
    return SimpleNamespace(
        title=f"Paper {index}",
        authors=[SimpleNamespace(name=f"Author {n}") for n in range(authors)],
        entry_id=f"https://arxiv.org/abs/test{index}",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary=f"Abstract {index}",
        primary_category="cs.RO",
        categories=["cs.RO", "cs.CV"],
    )


class FakeClient:
    def __init__(self, job):
        self.job = job
        self._session = requests.Session()

    def results(self, search):
        self.job.total = 2
        self.job.target = 2
        yield paper(1, 1)
        yield paper(2, 3)


def wait_job(client, identifier):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = client.get(f"/api/jobs/{identifier}").json
        if result["status"] in ("completed", "failed", "cancelled"):
            return result
        time.sleep(0.01)
    raise AssertionError("Job did not finish")


def test_browser_search_history_and_sort(tmp_path):
    app = create_app(tmp_path, FakeClient)
    try:
        with app.test_client() as client:
            assert client.get("/").status_code == 200
            for asset in ("app.css", "app.js"):
                assert client.get(f"/static/{asset}").status_code == 200
            options = {
                "keywords": "robot",
                "field": "title",
                "expression": "NOT survey",
                "limit": 10,
            }
            response = client.post("/api/search", json=options)
            assert response.status_code == 202
            job = wait_job(client, response.json["id"])
            assert job["status"] == "completed"
            papers = client.get(f"/api/jobs/{job['id']}/papers?sort=author_count").json["papers"]
            assert [len(item["authors"]) for item in papers] == [3, 1]
            assert all("summary" not in item for item in papers)
            assert papers[0]["primary_category"] == "cs.RO"
            assert papers[0]["subjects"] == [
                "Robotics (cs.RO)",
                "Computer Vision and Pattern Recognition (cs.CV)",
            ]
            history = client.get("/api/history?q=survey").json["records"]
            assert len(history) == 1
            assert history[0]["field"] == "title"
            saved = client.get(f"/api/history/{job['filename']}/papers").json
            assert saved["count"] == 2
            assert client.get(f"/api/history/{job['filename']}/download").status_code == 200
            download = client.get(f"/api/history/{job['filename']}/download?sort=author_count")
            assert [len(item["authors"]) for item in download.json] == [3, 1]
            assert client.post("/api/search", json={"expression": "x OR"}).status_code == 400
            assert (
                client.post("/api/search", json={"keywords": "x", "limit": -1}).status_code == 400
            )
            assert (
                client.post(
                    "/api/search", json={"keywords": "x", "include_abstract": "no"}
                ).status_code
                == 400
            )
            options["include_abstract"] = True
            job = wait_job(client, client.post("/api/search", json=options).json["id"])
            papers = client.get(f"/api/history/{job['filename']}/papers").json["papers"]
            assert papers[0]["summary"] == "Abstract 1"
    finally:
        app.extensions["search_jobs"].executor.shutdown(wait=True)


def test_partial_results_saved_on_network_error(tmp_path):
    class BrokenClient(FakeClient):
        def results(self, search):
            yield paper(1)
            raise requests.Timeout("Simulated network timeout")

    job = SearchJob({"query": "all:robot", "limit": 10, "include_abstract": False})
    storage = SearchStorage(tmp_path)
    job.run(storage, BrokenClient)
    assert job.status == "failed"
    assert "Timeout" in job.error
    assert len(storage.read(job.filename)) == 1
    assert storage.metadata(job.filename)["status"] == "failed"
    assert "Timeout" in storage.history()[0]["error"]
    assert any("Timeout" in event["message"] for event in storage.history()[0]["events"])


def test_cancel_saves_and_rejects_path_traversal(tmp_path):
    job = SearchJob({"query": "all:robot", "limit": 10, "include_abstract": False})
    job.cancel.set()
    storage = SearchStorage(tmp_path)
    job.run(storage, FakeClient)
    assert job.status == "cancelled"
    assert storage.read(job.filename) == []
    with pytest.raises(ValueError):
        storage.read("../outside.json")
    with pytest.raises(ValueError):
        storage.read("search.meta.json")


def test_progress_retry_and_total(monkeypatch):
    job = SearchJob({"query": "all:robot", "limit": 10, "include_abstract": False})
    job.cancel = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
    calls = []

    def request_page(self, url, first_page=True):
        calls.append(url)
        if len(calls) == 1:
            raise requests.Timeout("Simulated timeout")
        return SimpleNamespace(
            malformed=False, results=[paper(1)], header=SimpleNamespace(total_results=1)
        )

    import arxiv

    monkeypatch.setattr(arxiv.Client, "_parse_feed", request_page)
    client = ProgressClient(job)
    try:
        feed = client._parse_feed("https://export.arxiv.org/api/query?start=0")
        assert len(calls) == 2
        assert job.total == job.target == 1
        assert len(feed.results) == 1
        assert any("Timeout" in event["message"] for event in job.events)
        assert any("attempt 2/4" in event["message"] for event in job.events)
    finally:
        client._session.close()


def test_batch_history_delete_removes_only_selected_files(tmp_path):
    app = create_app(tmp_path, FakeClient)
    storage = app.extensions["search_storage"]
    names = [
        storage.save([{"title": f"Paper {n}", "authors": ["A"]}], {"keywords": str(n)})
        for n in range(3)
    ]
    originals = {name: (tmp_path / name).read_bytes() for name in names}
    try:
        with app.test_client() as client:
            response = client.post("/api/history/delete", json={"filenames": names[:2] + names[:1]})
            assert response.status_code == 200
            assert response.json["deleted"] == names[:2]
            assert [
                record["filename"] for record in client.get("/api/history").json["records"]
            ] == names[2:]
            for name in names[:2]:
                assert not (tmp_path / name).exists()
                assert not (tmp_path / (name + "_del")).exists()
                metadata = (tmp_path / name).with_suffix(".meta.json")
                assert not metadata.exists()
                assert not metadata.with_name(metadata.name + "_del").exists()
                assert client.get(f"/api/history/{name}/papers").status_code == 404
            assert (tmp_path / names[2]).read_bytes() == originals[names[2]]
            assert client.post("/api/history/delete", json={"filenames": []}).status_code == 400
            assert (
                client.post("/api/history/delete", json={"filenames": ["../bad.json"]}).status_code
                == 400
            )
    finally:
        app.extensions["search_jobs"].executor.shutdown(wait=True)


def test_batch_delete_checks_all_targets_before_deleting(tmp_path):
    storage = SearchStorage(tmp_path)
    name = storage.save([], {})
    with pytest.raises(FileNotFoundError):
        storage.delete_many([name, "missing.json"])
    assert (tmp_path / name).exists()
    result = storage.delete_many([name])
    assert result == {"deleted": [name], "errors": []}
    assert storage.history() == []
    assert list(tmp_path.iterdir()) == []


def test_batch_delete_reports_partial_failure(tmp_path, monkeypatch):
    storage = SearchStorage(tmp_path)
    names = [storage.save([], {}) for _ in range(2)]
    failed_source = tmp_path / names[1]
    unlink = Path.unlink

    def failing_unlink(source, *args, **kwargs):
        if source == failed_source:
            raise OSError("Simulated storage error")
        return unlink(source, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    result = storage.delete_many(names)
    assert result["deleted"] == names[:1]
    assert result["errors"] == [{"filename": names[1], "error": "Simulated storage error"}]
    assert not (tmp_path / names[0]).exists()
    assert not (tmp_path / names[0]).with_suffix(".meta.json").exists()
    assert (tmp_path / names[1]).is_file()
    assert (tmp_path / names[1]).with_suffix(".meta.json").is_file()
    assert len(storage.history()) == 1


def test_delete_rejects_metadata_symlink(tmp_path):
    storage = SearchStorage(tmp_path)
    name = "imported.json"
    (tmp_path / name).write_text("[]", encoding="utf-8")
    target = tmp_path / "external.txt"
    target.write_text("Keep this file", encoding="utf-8")
    (tmp_path / name).with_suffix(".meta.json").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic links"):
        storage.delete_many([name])
    assert (tmp_path / name).is_file()
    assert target.read_text(encoding="utf-8") == "Keep this file"


def test_subjects_primary_first_and_legacy_json(tmp_path):
    assert subject_labels(["cs.CV", "cs.RO", "cs.RO", "new.FIELD"], "cs.RO") == [
        "Robotics (cs.RO)",
        "Computer Vision and Pattern Recognition (cs.CV)",
        "new.FIELD",
    ]
    app = create_app(tmp_path, FakeClient)
    storage = app.extensions["search_storage"]
    legacy = storage.save([{"title": "Legacy", "authors": [], "categories": ["cs.RO"]}], {})
    missing = storage.save([{"title": "Missing", "authors": []}], {})
    try:
        with app.test_client() as client:
            papers = client.get(f"/api/history/{legacy}/papers").json["papers"]
            assert papers[0]["subjects"] == ["Robotics (cs.RO)"]
            assert "summary" not in papers[0]
            papers = client.get(f"/api/history/{missing}/papers").json["papers"]
            assert papers[0]["subjects"] == []
        assert "subjects" not in storage.read(legacy)[0]
    finally:
        app.extensions["search_jobs"].executor.shutdown(wait=True)


def test_yaml_controls_search_defaults_and_pagination(tmp_path):
    path = tmp_path / "browser.yaml"
    path.write_text(
        "server:\n  port: 0\n"
        "search:\n  default_limit: 2\n  max_limit: 3\n  include_abstract: true\n"
        "  api_page_size: 7\n  retries: 0\n  connect_timeout_seconds: 4\n  read_timeout_seconds: 9\n"
        "display:\n  papers_per_page: 1\n  sort_by_author_count: true\n"
        "storage:\n  directory: saved-papers\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.directory == tmp_path / "saved-papers"
    app = create_app(config=config, client_factory=FakeClient)
    try:
        with app.test_client() as client:
            html = client.get("/").get_data(as_text=True)
            assert 'max="3" value="2"' in html
            assert 'data-page-size="1"' in html
            response = client.post("/api/search", json={"keywords": "robot"})
            job = wait_job(client, response.json["id"])
            assert job["limit"] == 2
            assert (config.directory / job["filename"]).is_file()
            page = client.get(f"/api/jobs/{job['id']}/papers?page=2").json
            assert page["page_size"] == 1
            assert page["pages"] == 2
            assert len(page["papers"]) == 1
            assert page["papers"][0]["summary"] == "Abstract 2"
            assert (
                client.post("/api/search", json={"keywords": "robot", "limit": 4}).status_code
                == 400
            )
            actual_job = app.extensions["search_jobs"].get(job["id"])
            progress = ProgressClient(actual_job)
            try:
                assert progress.page_size == 2
                assert progress._session.config.read_timeout_seconds == 9
                assert actual_job.config.retries == 0
            finally:
                progress._session.close()
    finally:
        app.extensions["search_jobs"].executor.shutdown(wait=True)


@pytest.mark.parametrize(
    "text",
    [
        "search:\n  default_limit: 20\n  max_limit: 10\n",
        "server:\n  port: true\n",
        "search:\n  read_timeout_seconds: .nan\n",
        "display:\n  papers_per_page: 0\n",
        "search:\n  typo: 100\n",
        "storage:\n  directory: false\n",
        "search:\n  default_category: ../../other\n",
        "- invalid\n",
    ],
)
def test_invalid_yaml_settings(tmp_path, text):
    path = tmp_path / "invalid.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_date_query_uses_utc_and_covers_all_boolean_branches():
    config = load_config()
    now = datetime(2026, 1, 1, 1, 30, 45, tzinfo=timezone(timedelta(hours=8)))
    options = build_search_options(
        {"mode": "days", "recent_days": 2, "expression": "cat:cs.RO OR cat:cs.AI", "limit": 1},
        config,
        now=now,
    )
    assert (
        options["query"]
        == "((cat:cs.RO OR cat:cs.AI) AND submittedDate:[202512291730 TO 202512311730])"
    )
    assert options["limit"] is None
    assert options["date_start"] == "2025-12-29T17:30:00+00:00"
    assert options["date_end"] == "2025-12-31T17:30:00+00:00"
    assert options["recent_days"] == 2
    anchored = build_search_options({"mode": "days", "expression": "NOT survey"}, config, now=now)
    assert "submittedDate:" in anchored["query"] and "ANDNOT (all:survey)" in anchored["query"]
    date_only = build_search_options({"mode": "days", "recent_days": 1}, config, now=now)
    assert date_only["query"] == "submittedDate:[202512301730 TO 202512311730]"
    count = build_search_options({"keywords": "robot"}, config, now=now)
    assert count["mode"] == "count"
    assert count["limit"] == config.default_limit
    assert "submittedDate:" not in count["query"]


@pytest.mark.parametrize("days", [0, -1, True, "7", 1.5, None, 36501])
def test_invalid_day_window(days):
    with pytest.raises(ValueError, match="Recent days"):
        build_search_options({"mode": "days", "recent_days": days}, load_config())


@pytest.mark.parametrize("total", [0, 3])
def test_date_search_fetches_all_pages_and_saves_mode(tmp_path, monkeypatch, total):
    import arxiv
    from urllib.parse import parse_qs, urlparse

    config = replace(load_config(), default_limit=1, max_limit=1, api_page_size=1)
    calls = []

    def page(self, url, first_page=True):
        args = parse_qs(urlparse(url).query)
        start = int(args["start"][0])
        calls.append(start)
        assert "submittedDate:[" in args["search_query"][0]
        return SimpleNamespace(
            malformed=False,
            results=[paper(start)] if total else [],
            header=SimpleNamespace(total_results=total),
        )

    monkeypatch.setattr(arxiv.Client, "_parse_feed", page)
    app = create_app(tmp_path, config=config)
    try:
        with app.test_client() as client:
            response = client.post(
                "/api/search", json={"mode": "days", "recent_days": 7, "expression": "cat:cs.RO"}
            )
            assert response.status_code == 202
            job = wait_job(client, response.json["id"])
            assert job["status"] == "completed"
            assert job["limit"] is None
            assert job["target"] == job["fetched"] == total
            assert calls == list(range(total)) if total else calls == [0]
            history = client.get("/api/history").json["records"][0]
            assert history["mode"] == "days"
            assert history["recent_days"] == 7
            assert history["date_start"] == job["date_start"]
            assert history["date_end"] == job["date_end"]
            assert history["limit"] is None
            assert client.get(f"/api/history/{job['filename']}/papers").json["count"] == total
            assert client.post("/api/preview", json={"mode": "unknown"}).status_code == 400
            assert (
                client.post("/api/preview", json={"mode": "days", "recent_days": 0}).status_code
                == 400
            )
    finally:
        app.extensions["search_jobs"].executor.shutdown(wait=True)


def test_date_search_does_not_silently_truncate_api_limit(tmp_path, monkeypatch):
    import arxiv

    def page(self, url, first_page=True):
        return SimpleNamespace(
            malformed=False, results=[paper(1)], header=SimpleNamespace(total_results=300001)
        )

    monkeypatch.setattr(arxiv.Client, "_parse_feed", page)
    options = build_search_options({"mode": "days", "expression": "cat:cs.RO"}, load_config())
    job = SearchJob(options)
    job.run(SearchStorage(tmp_path))
    assert job.status == "failed"
    assert "Shorten the window" in job.error
    assert job.papers == []
