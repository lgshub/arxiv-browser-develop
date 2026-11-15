import argparse
import json
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, send_file

from .browser_config import DEFAULT_CONFIG_PATH, load_config
from .paper_metadata import subject_labels
from .paper_sorting import sort_papers
from .query_language import QueryError
from .search_options import build_search_options
from .search_jobs import JobManager
from .search_storage import SearchStorage


def create_app(search_dir=None, client_factory=None, config=None):
    config = config or load_config()
    web = Path(__file__).with_name("web")
    app = Flask(__name__, template_folder=str(web / "templates"), static_folder=str(web / "static"))
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024
    storage = SearchStorage(
        search_dir if search_dir is not None else config.directory,
        default_limit=config.default_limit,
    )
    manager = JobManager(
        storage, config=config, **({"client_factory": client_factory} if client_factory else {})
    )
    app.extensions["search_storage"] = storage
    app.extensions["search_jobs"] = manager
    app.extensions["browser_config"] = config

    def options():
        return build_search_options(request.get_json(silent=True), config)

    def paper_page(papers):
        method = request.args.get("sort", "original")
        if method == "author_count":
            papers = sort_papers(papers)
        elif method != "original":
            raise ValueError("Unknown sorting rule.")
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            raise ValueError("Page must be an integer.") from None
        size = config.papers_per_page
        pages = max(1, (len(papers) + size - 1) // size)
        page = min(page, pages)
        visible = []
        for paper in papers[(page - 1) * size : page * size]:
            record = dict(paper)
            if "subjects" not in record:
                record["subjects"] = subject_labels(
                    record.get("categories", []), record.get("primary_category", "")
                )
            visible.append(record)
        return jsonify(
            papers=visible,
            count=len(papers),
            page=page,
            pages=pages,
            page_size=size,
        )

    @app.errorhandler(ValueError)
    @app.errorhandler(QueryError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(FileNotFoundError)
    def missing(error):
        return jsonify(error="This result file was not found."), 404

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error="The request is too large."), 413

    @app.get("/")
    def index():
        return render_template("index.html", settings=config)

    @app.post("/api/preview")
    def preview():
        return jsonify(query=options()["query"])

    @app.post("/api/search")
    def search():
        job = manager.submit(options())
        return jsonify(job.snapshot()), 202

    @app.get("/api/jobs/<identifier>")
    def job_status(identifier):
        job = manager.get(identifier)
        if job is None:
            return jsonify(
                error="Search is no longer in memory. Open its saved results from history."
            ), 404
        return jsonify(job.snapshot())

    @app.post("/api/jobs/<identifier>/cancel")
    def cancel(identifier):
        job = manager.get(identifier)
        if job is None:
            return jsonify(error="Search not found."), 404
        job.cancel.set()
        return jsonify(message="Cancellation requested. Waiting for the current request to finish.")

    @app.get("/api/jobs/<identifier>/papers")
    def job_papers(identifier):
        job = manager.get(identifier)
        if job is None:
            return jsonify(error="Search not found."), 404
        with job.lock:
            papers = list(job.papers)
        return paper_page(papers)

    @app.get("/api/history")
    def history():
        return jsonify(records=storage.history(request.args.get("q", "")))

    @app.post("/api/history/delete")
    def delete_history():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object containing filenames.")
        try:
            result = storage.delete_many(data.get("filenames"))
        except FileNotFoundError:
            raise
        except OSError as error:
            return jsonify(error=f"Could not delete selected searches: {error}"), 500
        return jsonify(result)

    @app.get("/api/history/<name>/papers")
    def saved_papers(name):
        return paper_page(storage.read(name))

    @app.get("/api/history/<name>/download")
    def download(name):
        method = request.args.get("sort", "original")
        if method == "author_count":
            papers = sort_papers(storage.read(name))
            return Response(
                json.dumps(papers, ensure_ascii=False, indent=2) + "\n",
                mimetype="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="papers_by_author_count.json"'
                },
            )
        if method != "original":
            raise ValueError("Unknown sorting rule.")
        return send_file(
            storage.result_path(name),
            as_attachment=True,
            download_name=name,
            mimetype="application/json",
        )

    return app


def main():
    parser = argparse.ArgumentParser(description="Browse arXiv papers and saved searches")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host", help="Override server.host from the YAML configuration")
    parser.add_argument(
        "--port",
        type=int,
        help="Override server.port from the YAML configuration (0 selects a free port)",
    )
    parser.add_argument(
        "--data-dir", type=Path, help="Override storage.directory from the YAML configuration"
    )
    args = parser.parse_args()
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    app = create_app(args.data_dir, config=config)
    app.run(
        host=args.host if args.host is not None else config.host,
        port=args.port if args.port is not None else config.port,
        threaded=True,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
