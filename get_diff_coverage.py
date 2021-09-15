#!/usr/bin/env python3

import argparse
import math
import re
import subprocess
import xml.etree.ElementTree as xml_parser  # noqa
from collections import Counter, OrderedDict
from itertools import chain


def _parse_coverage(file_name):
    return xml_parser.parse(file_name).getroot()


def _get_coverage_map(tree, file):
    """
    Get a map of which lines are covered for a specified file
    :param tree: an element tree for the jacoco coverage report xml
    :param file: the file to find coverage for
    :return: a map of int -> boolean with a key of lines we have info for and value of whether or not it's covered
    """

    # skip files that are in the root directory
    if "/" not in file:
        return None

    # split file into group/package/filename as defined in jacoco's report
    file_info = re.search(r"(.*?)/src/.*?/((?:com|ca)/.*)\/(.*?\.java)", file)

    # skip files that don't match this
    if not file_info or len(file_info.groups()) != 3:
        return None

    group, package, file_name = file_info.groups()
    group = group.split("/")[-1]

    # look up the source file info in the report
    lookup = "group[@name='{}']/package[@name='{}']/sourcefile[@name='{}']".format(
        group, package, file_name
    )
    source_tree = tree.find(lookup)

    if not source_tree:
        print("Couldn't find a test coverage file for " + lookup)
        return []

    # search for lines with coverage information
    coverage_map = {}
    for line in source_tree.findall("line"):
        line_number = int(line.attrib["nr"])
        # We considered Covered = True if there are no missed instructions(mi) or missed branches(mb) in jacoco's info
        coverage_map[line_number] = (
            line.attrib["mi"] == "0" and line.attrib["mb"] == "0"
        )

    return coverage_map


def _get_git_diff(commit):
    """Get a diff between a specified commit(or branch) and HEAD"""
    return (
        subprocess.check_output(["git", "diff", commit, "HEAD", "-U0"])
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
        start, count = (int(x) for x in lines.split(","))
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
    Given an object with change and the coverage map for that file, produce information about coverage on lines changed
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


def get_file_message(file, buffer):
    # create file -> (list ranges of number)
    name = file["file"]
    covered_lines = file["coverage"]

    with open(name) as source_file:
        content = source_file.readlines()

    lines_to_display = []
    for line in file["uncovered_lines"]:
        for i in range(max(0, line - buffer), min(len(content), line + buffer + 1)):
            if i not in lines_to_display:
                lines_to_display.append(i)

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

    coverage = OrderedDict(sorted(coverage.items()))

    groups = []
    for i in coverage.keys():
        if not groups or i > groups[-1][-1] + 1:
            groups.append([i])
        else:
            groups[-1].append(i)

    if groups:
        message = f"🚗 {name}\n"
        for group in groups:
            for line in group:
                message += (
                    f"\t{coverage[line]} {str(line)}\t\t{content[line - 1][:-1]}\n"
                )
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


def get_target_diff_coverage(current_coverage, line_stats):
    """
    Gives some wiggle room for small MRs. If there are fewer than 5 uncovered lines, lower the
    required coverage to 75%-- this allows things like 2 uncovered lines in a 12-line MR. Otherwise,
    the diff coverage must be >= the current coverage in the main branch.
    """
    if line_stats["uncovered"] > 5:
        return current_coverage
    return 0.75


def get_diff_coverage(coverage_xml, commit, current_coverage):
    """
    Given the jacoco coverage xml and a commit to diff against, find the percent of lines added/changed that were
    covered
    """
    diff_changes = _parse_diff(_get_git_diff(commit))
    coverage_report = _parse_coverage(coverage_xml)

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
    target_coverage = get_target_diff_coverage(current_coverage, line_stats)
    if total_coverage < target_coverage:
        lines_required = get_required_lines_for_coverage(
            target_coverage, total_coverage, line_stats
        )
        message = (
            f"\n❗Coverage of {100 * total_coverage}% did not meet target of {100 * target_coverage}%.❗\n"
            + f"❗You require at least {lines_required} more line{'s' if lines_required > 1 else ''} of coverage❗\n\n"
            + get_untested_line_info(diff_changes, coverage_report, 4)
        )  # buffer size here is arbitrary

    return total_coverage * 100, target_coverage * 100, message


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jacoco_xml", help="The jacoco report aggregate xml file")
    parser.add_argument("commit", help="The commit hash or branch to diff against")
    parser.add_argument("target_coverage", help="The target coverage percent")
    args = parser.parse_args()
    coverage, _, message = get_diff_coverage(
        args.jacoco_xml, args.commit, float(args.target_coverage)
    )
    print(f"Coverage={coverage * 100}%")
    if message:
        print(message)


if __name__ == "__main__":
    main()
