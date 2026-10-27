# SPDX-License-Identifier: Apache-2.0
"""Entry script for the packaged executable.

Kept separate from ``src/cc_convert/__main__.py`` so PyInstaller has a plain
script to analyse. ``main()`` also dispatches the internal ``__daemon`` /
``__worker`` arguments that frozen builds use to re-enter the runner processes.
"""

from cc_convert.main import main

if __name__ == "__main__":
    main()
