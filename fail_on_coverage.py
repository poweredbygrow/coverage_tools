#!/usr/bin/env python3

import argparse
import subprocess
import sys
import time

import get_diff_coverage
import get_exact_coverage
import requests

PIPELINE_STATUS_URL = "https://gitlab.com/api/v4/projects/{0}/repository/commits/{1}/statuses?stage=coverage"
PIPELINE_MERGE_REQUEST_URL = (
    "https://gitlab.com/api/v4/projects/{0}/merge_requests?source_branch={1}"
)
DEFAULT_TARGET_BRANCH = "main"

def get_merge_base(target_branch):
    target_branch = target_branch or DEFAULT_TARGET_BRANCH
    subprocess.check_call(["git", "fetch", "origin", target_branch])
    return (
        subprocess.check_output(
            ["git", "merge-base", "HEAD", "origin/" + target_branch]
        )
        .decode()
        .strip()
    )


def is_before_coverage(commit_status):
    return "test" in commit_status["name"] or "coverage" in commit_status["name"]


def is_running(commit):
    return commit["status"] == "running"


def has_coverage(commit):
    return (
        "coverage" in commit["name"]
        and commit["status"] == "success"
        and commit["coverage"] is not None
    )


def get_results(api_url, gitlab_project_id, reference_hash, gitlab_token):
    params = {"private_token": gitlab_token, "membership": "yes"}
    request = requests.get(
        api_url.format(gitlab_project_id, reference_hash), params=params
    )
    print("status code:", request.status_code)
    if request.status_code != 200:
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
            raise Exception("Reference hash has no successful jobs " + reference_hash)
        else:
            # no jobs found, this is a problem
            raise Exception("Could not find job for reference hash " + reference_hash)

    latest = sorted(matching_statuses, key=lambda commit: commit["created_at"])[-1]
    return latest["coverage"]


def get_target_branch(gitlab_project_id, current_branch, gitlab_token):
    merge_request = get_results(
        PIPELINE_MERGE_REQUEST_URL, gitlab_project_id, current_branch, gitlab_token
    )
    if merge_request and "target_branch" in merge_request[0]:
        return merge_request[0]["target_branch"]
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gitlab_project_id", help="The Gitlab project id")
    parser.add_argument("gitlab_token", help="The Gitlab API token")
    parser.add_argument("current_branch", help="The current branch ref")
    parser.add_argument(
        "coverage_module", default="api-test", help="The module containing coverage"
    )
    parser.add_argument(
        "override_threshold",
        nargs="?",
        default=None,
        help="A threshold to bootstrap the pipeline with",
    )
    args = parser.parse_args()

    project_id = args.gitlab_project_id
    branch = args.current_branch
    token = args.gitlab_token
    override_threshold = (
        None if args.override_threshold is None else float(args.override_threshold)
    )

    target_branch = get_target_branch(project_id, branch, token)

    if override_threshold:
        reference_hash = "(using override coverage)"
        coverage = override_threshold
    else:
        reference_hash = get_merge_base(target_branch)
        coverage = get_latest_coverage(project_id, reference_hash, token)

    coverage = round(coverage, 4)
    print(f"coverage on reference hash {reference_hash}={coverage}")
    coverage_html_filename = (
        args.coverage_module + "/target/site/jacoco-aggregate/index.html"
    )
    current_coverage = round(
        get_exact_coverage.get_exact_coverage(coverage_html_filename), 4
    )
    print(f"current_coverage on HEAD={current_coverage}")

    if current_coverage < coverage and not override_threshold:
        diff_coverage, target_coverage, message = get_diff_coverage.get_diff_coverage(
            args.coverage_module + "/target/site/jacoco-aggregate/jacoco.xml",
            reference_hash,
            coverage / 100,
        )
        if diff_coverage < target_coverage:
            print(
                "Overall coverage has decreased and diff coverage {}% is below the target coverage of {}%".format(
                    diff_coverage, target_coverage
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
