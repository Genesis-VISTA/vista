"""
The user a service-level test acts as. Services take the user explicitly
(`db/ownership.py`); a test that calls one directly, on a desktop-shaped
database, acts as the implicit user `local`, whose row `init_db` and the
`engine` fixture make.

Transient (never added to a session): the data layer reads only its id.
"""

from vista_backend.db.schemas import LOCAL_USER_ID, UserTable

LOCAL = UserTable(id=LOCAL_USER_ID)
