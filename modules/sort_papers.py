import argparse

from .paper_sorting import SORTERS, sort_json_file


def main():
    parser = argparse.ArgumentParser(description="按指定规则排序论文 JSON")
    parser.add_argument("input", help="输入 JSON 文件")
    parser.add_argument("-o", "--output", help="输出 JSON 文件，默认在原文件旁另存")
    parser.add_argument("--sort", choices=sorted(SORTERS), default="author_count")
    parser.add_argument("--ascending", action="store_true", help="改为从少到多排序")
    args = parser.parse_args()

    try:
        output, count = sort_json_file(
            args.input,
            args.output,
            args.sort,
            descending=not args.ascending,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f"已按 {args.sort} 排序 {count} 篇论文：{output}")


if __name__ == "__main__":
    main()
