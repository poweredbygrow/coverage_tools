#!/usr/bin/env python3

import argparse
import subprocess  # nosec
import sys
import time

import requests

import get_diff_coverage2

PIPELINE_STATUS_URL = (
    "https://gitlab.com/api/v4/projects/{0}/repository/commits/{1}/statuses"
)
PIPELINE_MERGE_REQUEST_URL = (
    "https://gitlab.com/api/v4/projects/{0}/merge_requests?source_branch={1}"
)
COVERAGE_XML_FILENAME = "target/coverage.xml"
ORIGIN_MAIN_BRANCH = "origin/main"


def get_merge_base():
    return (
        subprocess.check_output(["git", "merge-base", "HEAD", ORIGIN_MAIN_BRANCH])  # nosec
        .decode()
        .strip()
    )


def is_before_coverage(commit_status):
    return "test" in commit_status["name"] or "coverage" in commit_status["name"]


def is_running(commit):
    return commit["status"] == "running"


def has_coverage(commit):
    return commit["status"] == "success" and commit["coverage"] is not None


def get_results(api_url, gitlab_project_id, reference_hash, gitlab_token):
    params = {"private_token": gitlab_token, "membership": "yes"}
    request = requests.get(
        api_url.format(gitlab_project_id, reference_hash), params=params
    )
    if request.status_code != 200:
        print("status code:", request.status_code)
        raise Exception("Could not find any commit status for hash: " + reference_hash)
    return request.json()


def get_latest_coverage(gitlab_project_id, reference_hash, gitlab_token):
    statuses = get_results(
        PIPELINE_STATUS_URL, gitlab_project_id, reference_hash, gitlab_token
    )
    waiting = False
    while True:
        matching_statuses = [s for s in statuses if has_coverage(s)]
        if matching_statuses:
            break
        if any(is_before_coverage(s) and is_running(s) for s in statuses):
            if not waiting:
                print("Reference commit is running, wait for it to finish")
                waiting = True
            else:
                print(".", end="", flush=True)
            time.sleep(20)
            statuses = get_results(
                PIPELINE_STATUS_URL, gitlab_project_id, reference_hash, gitlab_token
            )
        elif statuses:
            # no coverage because all jobs failed
            raise Exception("Target branch has no successful jobs")
        else:
            # no jobs found, this is a problem
            raise Exception("Could not find job for reference hash " + reference_hash)

    latest = sorted(matching_statuses, key=lambda commit: commit["created_at"])[-1]
    return latest["coverage"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gitlab_project_id", help="The Gitlab project id")
    parser.add_argument("gitlab_token", help="The Gitlab API token")
    parser.add_argument("current_branch", help="The current branch ref")
    args = parser.parse_args()

    project_id = args.gitlab_project_id
    branch = args.current_branch
    token = args.gitlab_token

    print(
        f"To test locally, run 'python -m fail_on_coverage2 {project_id} API_TOKEN_HERE {branch}'"
    )

    current_coverage = round(
        get_diff_coverage2.get_total_coverage(COVERAGE_XML_FILENAME), 4
    )
    print(f"coverage on HEAD of current branch is {current_coverage}%")

    reference_hash = get_merge_base()
    print(f"Merge base is {reference_hash}")
    coverage = get_latest_coverage(project_id, reference_hash, token)

    coverage = round(coverage, 4)
    print(f"coverage on reference hash {reference_hash} is {coverage}%")

    if current_coverage < coverage:
        print(
            f"Overall coverage has decreased by {round(current_coverage - coverage, 4)}%"
        )
        diff_coverage, _, message = get_diff_coverage2.get_diff_coverage(
            COVERAGE_XML_FILENAME, reference_hash, coverage / 100
        )
        print(f"Diff coverage: {diff_coverage}%")
        if diff_coverage < coverage:
            print(
                "Overall coverage has decreased and diff coverage {}% is below the target coverage of {}%".format(
                    diff_coverage, coverage
                )
            )
            print(message)
            sys.exit(1)
        elif current_coverage < coverage - 10:
            print(
                "Coverage has decreased by more than 10%, but diff coverage was ok."
                + " Check whether the test pipelines ran properly."
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
