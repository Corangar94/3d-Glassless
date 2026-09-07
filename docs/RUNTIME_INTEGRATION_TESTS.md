# Runtime integration regressions

The native `runtime_integration_tests` target compiles production control paths extracted by CMake from `depth_infer.cpp` and `overlay.cpp`. Each start marker must be unique, source changes trigger regeneration, and CRLF input is supported. It does not maintain a second copy of the scheduler or publication logic.

The test boundaries replace the GPU texture/context, clock, model output, and OS window calls. The actual scheduler, `run_once`, tile update/atlas loops, worker handoff, publication metadata, freshness gate, visibility predicate, held-frame match, and recovery policy are executed. Assertions remain active in Release builds.

Ten groups cover first-frame visibility, empty polling and failures, fast-mode 2/3/5-tile coverage, late complete-held results, late partial and obsolete results, busy/rate-limited capture replacement, post-success worker failure/backoff, outstanding-work timeout versus healthy idle capture, sustained fast-mode freshness, and generation/timestamp upgrade rules. Repeated idle polling must not manufacture new capture identities or unbounded completion work.

Portable build without vendor downloads:

```sh
cmake -S overlay -B build/runtime-tests -DG3D_PORTABLE_TESTS_ONLY=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build/runtime-tests --parallel 2
ctest --test-dir build/runtime-tests --output-on-failure
```

The outstanding-work watchdog uses an adaptive 5–15 second deadline and is armed only while inference work is pending/running; an unchanged scene with no outstanding work remains healthy. Fast mode schedules the least-recently completed tile so available one-tile inference capacity is shared across the frame instead of repeatedly preferring the center.

The existing required Windows native-build workflow runs this target through CTest alongside the shader tests. The portable CMake configuration supports running the same deterministic integration checks on other platforms without vendor dependencies. The existing Linux policy workflow is unchanged.

`tests/test_live_filter_runtime_integration.py` runs the complete Python tracking loop with real runtime constructors, frame processing, and an instrumented `AdaptivePoseFilter`. Fake capture/settings inputs cover configured-value retention, unavailable and invalid data, version suppression, custom admission bounds, throttling, and reader recovery. The base full-settings path must not bypass controller ownership.

These are deterministic integration tests, not live webcam, GPU/DirectML fault injection, or visual-quality acceptance. Hardware testing is still required for visual artifacts, driver-specific recovery, and performance.
