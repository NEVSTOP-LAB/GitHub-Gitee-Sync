#!/bin/sh
# Thin shell shim: delegates to entrypoint.py
#
# GitHub Actions Docker container actions set inputs as INPUT_{NAME} where
# {NAME} is the uppercased input name with hyphens PRESERVED.  POSIX shell
# drops environment variable names that contain hyphens, so the mapping must
# be done in Python (which reads os.environ directly and supports any name).
exec python3 /app/entrypoint.py "$@"
