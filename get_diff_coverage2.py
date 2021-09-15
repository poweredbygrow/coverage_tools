#!/usr/bin/env python3

import argparse
import math
import re
import subprocess  # nosec
import xml.etree.ElementTree as element_tree  # nosec
from collections import Counter, OrderedDict
from itertools import chain

IGNORED_PACKAGES = [".venv/", "target/"]

def _parse_coverage(file_name):
    return element_tree.parse(file_name).getroot()  # nosec


def _get_coverage_map(tree, file):
    """
    Get a map of which lines are covered for a specified file
    :param tree: an element tree for the coverage report xml
    :param file: the file to find coverage for
    :return: a map of int -> boolean with a key of lines we have info for and value of whether or not it's covered
    """

    # skip files that are in an ignored package or in the root directory
    if any(file.startswith(package) for package in IGNORED_PACKAGES) or "/" not in file:
        return None

    # split file into package/filename as defined in jacoco's report
    file_info = re.search(r"(.*)\/(.*\.py)", file)

    # skip files that don't match this, for example templates/info.html
    if not file_info or len(file_info.groups()) != 2:
        return None

    package, file_name = file_info.groups()

    # look up the source file info in the report
    lookup = 'package[@name="{}"]/classes/class[@name="{}"]'.format(
        package.replace("/", "."), file_name
    )
    source_tree = tree.find(lookup)

    if not source_tree:
        print("Couldn't find a test coverage file for " + lookup)
        return []

    # search for lines with coverage information
    coverage_map = {}
    for line in source_tree.find("lines").findall("line"):
        line_number = int(line.attrib["number"])
        coverage_map[line_number] = line.attrib["hits"] == "1"

    return coverage_map


def _get_git_diff(commit):
    """Get a diff between a specified commit(or branch) and HEAD"""
    return (
        subprocess.check_output(["git", "diff", commit, "HEAD", "-U0"])  # nosec
        .decode(errors="ignore")
        .strip()
    )


def _get_lines_changed(line_summary):
    """
    Parse the line diff summary into a list of numbers representing line numbers added or changed
    :param line_summary: the summary from a git diff of lines that have changed (ex: @@ -1,40 +1,23 @@)
    :return: a list of integers indicating which lines changed for that summary
    """
    lines = re.search(r"\@\@.*?\+(.+?) \@\@", line_summary).group(1)
    if "," in lines:
        start, count = [int(x) for x in lines.split(",")]
        return list(range(start, start + count))
    return [int(lines)]


def _parse_file_diff(diff):
    """Parse a single file's diff, return an object of that files name and the lines changed"""
    file_name_info = re.search(r".*\+\+\+ b/(.+?)\s+", diff)

    if not file_name_info or not file_name_info.group(1):
        return None
    file_name = file_name_info.group(1)

    # find mapping of which lines where changed
    diff_line_summaries = re.findall(r"\@\@.*?\@\@", diff)

    # add line
    added_lines = list(
        chain.from_iterable([_get_lines_changed(s) for s in diff_line_summaries])
    )

    return {"file": file_name, "lines_changed": added_lines}


def _parse_diff(diff):
    """Parse the raw diff string into a set of objects containing the file name and changed lines"""
    file_diffs = re.split(r"\ndiff --git ", diff)
    return [
        file_info
        for file_info in [_parse_file_diff(file_diff) for file_diff in file_diffs]
        if file_info is not None
    ]


def _reconcile_coverage(change, coverage_map):
    """
    Given an object with change and the coverage map for that file, produce information about coverage on lines
    changed.

    :param change: an object containing the file name and list of changed/added lines
    :param coverage_map: a map int->boolean of line numbers to coverage status
    :return: a counter of covered/uncovered/ignored lines
    """
    line_stats = Counter()

    for line in change["lines_changed"]:
        if line not in coverage_map:
            line_stats["ignored"] += 1
        else:
            if coverage_map[line]:
                line_stats["covered"] += 1
            else:
                line_stats["uncovered"] += 1

    return line_stats


def get_coverage(line_stats):
    denominator = line_stats["covered"] + line_stats["uncovered"]
    if denominator == 0:
        return None
    return float(line_stats["covered"]) / denominator


def get_lines_to_display(file, buffer, content):
    lines_to_display = []
    for line in file["uncovered_lines"]:
        for i in range(max(0, line - buffer), min(len(content), line + buffer + 1)):
            if i not in lines_to_display:
                lines_to_display.append(i)
    return lines_to_display


def get_coverage_icons(lines_to_display, covered_lines, file):
    coverage = {}
    for line in lines_to_display:
        if line not in covered_lines:
            coverage[line] = "  "
        elif line in file["lines_changed"]:
            coverage[line] = (
                "✅" if line in covered_lines and covered_lines[line] else "❌"
            )
        else:
            coverage[line] = (
                "✔️ " if line in covered_lines and covered_lines[line] else "✖️ "
            )

    return OrderedDict(sorted(coverage.items()))


def get_file_message(file, buffer):
    # create file -> (list ranges of number)
    name = file["file"]
    covered_lines = file["coverage"]

    with open(name) as source_file:
        content = source_file.readlines()

    lines_to_display = get_lines_to_display(file, buffer, content)
    coverage_icons = get_coverage_icons(lines_to_display, covered_lines, file)

    groups = []
    for i in coverage_icons.keys():
        if not groups or i > groups[-1][-1] + 1:
            groups.append([i])
        else:
            groups[-1].append(i)

    if groups:
        message = f"🚗 {name}\n"
        for group in groups:
            for line in group:
                message += f"\t{coverage_icons[line]} {str(line)}\t\t{content[line - 1][:-1]}\n"
            message += "\n"
        return message
    return ""


def get_untested_line_info(diff_changes, coverage_report, buffer):
    """Gets a message which contains untested lines in the commit"""
    untested_lines = []
    for change in diff_changes:
        coverage_map = _get_coverage_map(coverage_report, change["file"])
        # no coverage = entirely untested
        if coverage_map is None:
            continue

        uncovered_lines = [
            line
            for line in change["lines_changed"]
            if line in coverage_map and not coverage_map[line]
        ]
        untested_lines.append(
            {
                "file": change["file"],
                "lines_changed": change["lines_changed"],
                "uncovered_lines": uncovered_lines,
                "coverage": coverage_map,
            }
        )

    return "\n".join([get_file_message(file, buffer) for file in untested_lines])


def get_required_lines_for_coverage(target_coverage, total_coverage, line_stats):
    missing_coverage = target_coverage - total_coverage
    line_count = line_stats["covered"] + line_stats["uncovered"]
    return math.ceil(missing_coverage * line_count)


def get_diff_coverage(coverage_xml, commit, target_coverage):
    """
    Given the coverage xml and a commit to diff against, find the percent of lines added/changed that were
    covered
    """

    diff_changes = _parse_diff(_get_git_diff(commit))
    coverage_report = _parse_coverage(coverage_xml).find("packages")

    file_stats = {}
    line_stats = Counter()

    # find coverage across git diff
    for change in diff_changes:
        coverage_map = _get_coverage_map(coverage_report, change["file"])
        if coverage_map is not None:
            file_stats[change["file"]] = _reconcile_coverage(change, coverage_map)
            line_stats += file_stats[change["file"]]

    total_coverage = get_coverage(line_stats)
    if total_coverage is None:
        # if you can't match any, assume adding tests
        print("Couldn't get any coverage!")
        total_coverage = 1

    message = None
    if total_coverage < target_coverage:
        lines_required = get_required_lines_for_coverage(
            target_coverage, total_coverage, line_stats
        )
        message = (
            f"\n❗Coverage of {100*total_coverage}% did not meet target of {100*target_coverage}%.❗\n"
            + f"❗You require at least {lines_required} more lines of coverage❗\n\n"
            + get_untested_line_info(diff_changes, coverage_report, 4)
        )  # buffer size here is arbitrary

    return total_coverage * 100, file_stats, message


def get_total_coverage(coverage_xml):
    coverage_report = _parse_coverage(coverage_xml)
    return float(coverage_report.attrib["line-rate"]) * 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_xml", help="The coverage report xml file")
    parser.add_argument("commit", help="The commit hash or branch to diff against")
    parser.add_argument("target_coverage", help="The target coverage percent")
    args = parser.parse_args()
    coverage, _, message = get_diff_coverage(
        args.coverage_xml, args.commit, float(args.target_coverage)
    )
    print(f"Coverage={coverage}%")
    if message:
        print(message)


if __name__ == "__main__":
    main()
