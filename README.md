# coverage_tools
Just some tools for dealing with code coverage issues

Will this be maintained? Likely not. Should this be shared? Yes!

Everyone likes when their code is reasonably well covered, but nobody likes hard rules around coverage that force developers to game the system to pass coverage requirements. This repo exists to share some tools for getting around coverage requirements reasonably.

Heads up: this has been jankily snatched out of 2 codebases and not really reconciled or tidied up. You may require small tweaks to make this work.

Includes:

	get_exact_coverage.py
		gets exact coverage from a jacoco xml

	get_diff_coverage.py
		the original uses a jacoco xml and version 2 uses a python coverage xml. They've slightly diverged and could be combined if someone cares deeply about it.

		gets the coverage of the diff between the current branch and a specified commit hash, using a coverage report. Also this can print out a summary of the untested code changed in your commit, so you can easily see what changes are missing coverage. The main use case was that requiring code coverage to increase on every commit overall means that it's possible that replacing 500 lines of code with 50% coverage with 50 lines with 100% coverage drops the number of covered lines in the codebase, even if the functionality that was refactored has better coverage.


	fail_on_coverage.py
		Version 1 work with the java version, and checks exact coverage first, before falling back on diff coverage. Version 2 uses the python version of get_diff_coverage, and I don't think bothers to check overall coverage at all.