import json
from pathlib import Path


with Path(__file__).with_name("category_names.json").open(encoding="utf-8") as file:
    CATEGORY_NAMES = json.load(file)


def subject_labels(categories, primary_category=""):
    codes = list(dict.fromkeys(([primary_category] if primary_category else []) + list(categories)))
    return [
        f"{CATEGORY_NAMES[code]} ({code})" if code in CATEGORY_NAMES else code for code in codes
    ]


def paper_to_record(paper, include_abstract=False):
    record = {
        "title": paper.title,
        "authors": [author.name for author in paper.authors],
        "url": paper.entry_id,
        "published": paper.published.isoformat(),
        "primary_category": paper.primary_category,
        "categories": list(paper.categories),
        "subjects": subject_labels(paper.categories, paper.primary_category),
    }
    if include_abstract:
        record["summary"] = paper.summary
    return record
