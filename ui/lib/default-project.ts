/**
 * The default project and knowledge base the tour points at. The UI can't
 * import the backend's seed, so these repeat `HELLO_OLCF_PROJECT_NAME` and
 * `OLCF_DOCS_SLUG` in `backend/src/vista_backend/db/seed.py`; a backend test
 * (`test_default_project_constants.py`) fails when the two drift apart.
 * Keep each one a plain string literal on one line, which that test reads.
 */

/** The seeded project's name. */
export const HELLO_OLCF_PROJECT_NAME = "hello-olcf";

/** The slug of the seeded knowledge base, "OLCF User Documentation". */
export const OLCF_DOCS_SLUG = "olcf-docs";
