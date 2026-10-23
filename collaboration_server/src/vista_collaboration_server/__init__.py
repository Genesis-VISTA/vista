"""
VISTA's collaboration server: where researchers explicitly publish, and explicitly
import, projects, skills and knowledge bases.

The server knows nothing of what it stores. Everything is an *item* of some kind
with a list of immutable, numbered *versions*; what a version means is in its
manifest and blob, which VISTA backends write and read.
"""

API_VERSION = 1
"""
The version of the HTTP API this server speaks, served by `GET /version`.

Raised only for a change an older backend would misread. A backend refuses a
server whose API version is not the one it speaks, and says so, rather than
guessing at a shape it does not know.
"""
