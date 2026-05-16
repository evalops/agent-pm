BAZEL ?= bazelisk
BUILDIFIER ?= buildifier
UV ?= uv

BAZEL_TARGETS ?= //...
BAZEL_TEST_TARGETS ?= //:pytest
BAZEL_REMOTE_CONFIG ?= remote-gcp-dev
BAZEL_RBE_SMOKE_TARGETS ?= //:pytest
BAZEL_CI_REMOTE_DOWNLOAD_FLAGS ?= --remote_download_toplevel

.PHONY: requirements-lock bazel-format bazel-mod-tidy bazel-check bazel-test bazel-test-remote bazel-rbe-smoke

requirements-lock:
	$(UV) export --all-extras --no-emit-project --no-hashes --format requirements-txt --output-file requirements_lock.txt

bazel-format:
	$(BUILDIFIER) -lint=fix BUILD.bazel MODULE.bazel bazel/platforms/BUILD.bazel

bazel-mod-tidy:
	$(BAZEL) mod tidy

bazel-check: requirements-lock bazel-format bazel-mod-tidy
	git diff --exit-code -- BUILD.bazel MODULE.bazel MODULE.bazel.lock bazel/platforms/BUILD.bazel requirements_lock.txt

bazel-test:
	$(BAZEL) test $(BAZEL_TEST_TARGETS)

bazel-test-remote:
	$(BAZEL) test --config=$(BAZEL_REMOTE_CONFIG) $(BAZEL_CI_REMOTE_DOWNLOAD_FLAGS) $(BAZEL_RBE_SMOKE_TARGETS)

bazel-rbe-smoke:
	./scripts/run-bazel-rbe.sh -- $(MAKE) bazel-test-remote
