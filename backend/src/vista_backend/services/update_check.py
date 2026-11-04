"""
Whether a newer VISTA release exists on GitHub (openspec change `in-app-updates`).

The installed version is `Settings.version`: the release tag for a release
package, or one of the shapes a build that is not a release carries, which
never checks (`is_dev_build`).
"""

import re

_RELEASE = re.compile(r"^v?\d+(\.\d+)*(-?(a|b|rc)\d+)?$")
"""
A release version as tags spell them: `0.2.3`, `v0.2.3`, `0.2.2-rc1`. Anything
else is a build that was not released: `dev` on a checkout, `0.2.0+3.g1a2b3c4`
from a local package build, `-dirty` from an uncommitted tree, `unknown` from a
package with no VERSION file.
"""


def is_dev_build(version: str) -> bool:
    """True for a version no release carries, which is never checked for updates."""
    return not _RELEASE.match(version.strip())
