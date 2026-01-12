# =============================================================================
# Project: Distman - Simple File Distribution Manager
# Makefile for building project executables on Linux
#
# Usage:
#   make           - Builds targets
#   make clean     - Removes all build artifacts
#   make build     - Builds the requirements for Linux
#   make install   - Installs the build artifacts using distman
#
# Requirements:
#   - Python and pip installed (Linux)
#   - Wine installed for Windows builds on Linux
#   - distman installed for installation (pip install distman)
# =============================================================================

# Define the installation command
BUILD_DIR := build
BUILD_CMD := python -m pip install . -t $(BUILD_DIR)

# Target to build for Linux
build: clean
	$(BUILD_CMD)
	rm -rf build/bin build/lib build/distman build/bdist* build/__pycache__

# Clean target to remove the build directory
clean:
	rm -rf build

# Install target to install the builds using distman
dryrun:
	dist --dryrun

# Install target to install the builds using distman
install: build
	dist --yes

# Phony targets
.PHONY: build dryrun install clean
