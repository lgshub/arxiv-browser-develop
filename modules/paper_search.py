import sys

import arxiv

from .paper_metadata import paper_to_record
from .browser_config import load_config
from .search_jobs import TimedSession


def fetch_recent_papers(limit=None, category=None, config=None):
    config = config or load_config()
    limit = config.default_limit if limit is None else limit
    category = config.default_category if category is None else category
    if type(limit) is not int or not 1 <= limit <= config.max_limit:
        raise ValueError(f"limit 必须为 1 到 {config.max_limit} 之间的整数")

    client = arxiv.Client(
        page_size=config.api_page_size,
        delay_seconds=config.request_interval_seconds,
        num_retries=config.retries,
    )
    client._session.close()
    client._session = TimedSession(config)
    search = arxiv.Search(
        query=f"cat:{category}",
        max_results=limit,
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )

    papers = []
    try:
        for paper in client.results(search):
            papers.append(paper_to_record(paper, config.include_abstract))
            if len(papers) % config.api_page_size == 0:
                print(f"已获取 {len(papers)}/{limit} 篇", file=sys.stderr, flush=True)
    finally:
        client._session.close()
    return papers
