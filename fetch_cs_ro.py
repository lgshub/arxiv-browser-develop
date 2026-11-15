import json

from modules.browser_config import load_config
from modules.paper_search import fetch_recent_papers


def main():
    config = load_config()
    config.directory.mkdir(parents=True, exist_ok=True)
    category = config.default_category.lower().replace(".", "_")
    output = config.directory / f"{category}_recent_{config.default_limit}.json"
    if output.exists():
        raise FileExistsError(f"输出文件已存在：{output}")

    papers = fetch_recent_papers(config=config)
    with output.open("x", encoding="utf-8") as file:
        json.dump(papers, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"已保存 {len(papers)} 篇论文：{output}")


if __name__ == "__main__":
    main()
