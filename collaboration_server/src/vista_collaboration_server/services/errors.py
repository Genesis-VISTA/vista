"""The refusals services raise, each answered with its own status by `api/app.py`."""


class NotFound(Exception):
    """
    `404`: no such item, version, team or user; the message says which. Also
    what something the caller may not see is, so that refusing it says nothing
    about whether it exists.
    """


class Conflict(Exception):
    """`409`: the request clashes with what is there (a team's name, its last member)."""


class Invalid(Exception):
    """`400`: the request makes no sense here (managing the public team's members)."""
