# Build and signing separation

The workflow compiles source in read-only jobs, binds the resulting platform
artifacts by digest, and performs signing and publication only on fresh runners
that do not check out private source.

The executable contracts live in `tests/test_confidential_output.py` and
`tests/test_release_workflow.py`. They reject authority crossing into the source
build, mutable artifact handoffs, private build controls entering the signing
phase, and mismatched build-toolchain pins.

Release execution remains staged through the repository's existing draft and
approval controls.
