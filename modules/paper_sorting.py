import json
from pathlib import Path


def sort_by_author_count(papers, *, descending=True):
    for index, paper in enumerate(papers):
        if not isinstance(paper.get("authors"), list):
            raise ValueError(f"第 {index + 1} 篇论文的 authors 必须是列表")
    return sorted(papers, key=lambda paper: len(paper["authors"]), reverse=descending)


SORTERS = {
    "author_count": sort_by_author_count,
}


def sort_papers(papers, method="author_count", *, descending=True):
    if not isinstance(papers, list) or any(
        not isinstance(paper, dict) for paper in papers
    ):
        raise ValueError("JSON 顶层必须是论文对象组成的列表")
    if method not in SORTERS:
        raise ValueError(f"未知排序方式：{method}，可选：{', '.join(SORTERS)}")
    return SORTERS[method](papers, descending=descending)


def sort_json_file(input_path, output_path=None, method="author_count", *, descending=True):
    input_path = Path(input_path)
    if output_path is None:
        suffix = "" if descending else "_asc"
        output_path = input_path.with_name(f"{input_path.stem}_by_{method}{suffix}.json")
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError(f"输出文件已存在：{output_path}")

    with input_path.open(encoding="utf-8") as file:
        papers = json.load(file)
    sorted_papers = sort_papers(papers, method, descending=descending)
    with output_path.open("x", encoding="utf-8") as file:
        json.dump(sorted_papers, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return output_path, len(sorted_papers)
