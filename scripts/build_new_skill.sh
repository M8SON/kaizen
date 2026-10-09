#!/bin/bash
# build_new_skill.sh <skill_dir> <image>
#
# Called by core.install_pipeline.DockerBuilder after validation and the
# user's confirmations. Builds <skill_dir>/scripts/Dockerfile as <image>.
# This script holds the Docker socket access — Claude Code never does.

set -e

SKILL_DIR="$1"
IMAGE_NAME="$2"
if [ -z "$SKILL_DIR" ] || [ -z "$IMAGE_NAME" ]; then
    echo "Usage: build_new_skill.sh <skill_dir> <image>" >&2
    exit 1
fi

BUILD_DIR="$SKILL_DIR/scripts"
if [ ! -f "$BUILD_DIR/Dockerfile" ]; then
    echo "No Dockerfile found in $BUILD_DIR" >&2
    exit 1
fi

echo "Building $IMAGE_NAME..."
docker build -t "$IMAGE_NAME" "$BUILD_DIR"
echo "Done: $IMAGE_NAME"
