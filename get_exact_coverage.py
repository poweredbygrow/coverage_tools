#!/usr/bin/env python3

import argparse
import re

PAT = re.compile(
    r""".*\<td\>Total\<\/td\>\<td class=\"bar\"\>([0-9,]+) of ([0-9,]+)\<\/td\>.*"""
)


def get_exact_coverage(jacoco_index_html):
    content = open(jacoco_index_html).read()
    matches = PAT.match(content)
    numerator, denominator = matches.groups()
    numerator, denominator = int(numerator.replace(",", "")), int(
        denominator.replace(",", "")
    )
    print(f"Number of missed lines={numerator}")
    print(f"Number of total lines={denominator}")
    if denominator == 0:
        return 100
    coverage = (1 - numerator / denominator) * 100
    return coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jacoco_index_html", help="The jacoco report index.html file")
    args = parser.parse_args()
    coverage = get_exact_coverage(args.jacoco_index_html)
    print(f"Coverage={coverage}%")


if __name__ == "__main__":
    main()
