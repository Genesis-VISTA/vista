## Purpose

How the backend decides, at each start, whether an installation's database can serve the
current version of VISTA, and what it does with one that can't: refuse it untouched and say so,
so the researcher can reset it instead of meeting failures inside the application.

## ADDED Requirements

### Requirement: An incompatible database is refused at startup

On every start, before writing anything to an existing database, the backend SHALL check that
the database can serve the current models once missing tables and missing nullable columns are
added. A database that fails the check SHALL be refused: the backend SHALL NOT change it, SHALL
NOT serve requests, and SHALL end startup with the failure code `database-incompatible`. The
backend log SHALL name each table and column that failed.

#### Scenario: A database from v0.1.4

- **WHEN** a newer VISTA starts on a database last used by v0.1.4, whose tables still hold the
  NOT NULL columns that version wrote
- **THEN** startup ends with `database-incompatible`, the backend log names the offending
  columns, and the database file is unchanged

#### Scenario: A column the code needs is missing

- **WHEN** the database lacks a NOT NULL column without a default that the current models
  declare
- **THEN** startup ends with `database-incompatible` naming that table and column, and the
  database file is unchanged

#### Scenario: A database written by a newer VISTA

- **WHEN** an older VISTA starts on a database that a newer one changed incompatibly
- **THEN** startup ends with `database-incompatible` and the database file is unchanged

### Requirement: A compatible database starts as before

A database that passes the check SHALL start exactly as it does today, with missing tables and
nullable columns added. A missing or empty database SHALL be created as on a first run. Extra
tables, and extra nullable columns that the models don't declare, SHALL NOT make a database
incompatible.

#### Scenario: First run

- **WHEN** the backend starts with no database
- **THEN** it creates and seeds one as before

#### Scenario: An additive change

- **WHEN** the current models add a nullable column or a new table that the database lacks
- **THEN** the backend starts and adds it

#### Scenario: Extra nullable columns

- **WHEN** the database has a nullable column or a table that the models don't declare
- **THEN** the backend starts normally

### Requirement: The refusal says how to recover

The refusal SHALL be recorded where the package launcher can read its code when the backend has
exited. The backend log SHALL say that the database can be moved aside or removed to start
fresh, and where it is.

#### Scenario: An index of what the database held

- **WHEN** the backend refuses a database
- **THEN** it leaves, beside the database, a list of the projects, skills and knowledge bases
  the database records, with their names and where their files are, without changing the
  database
- **AND** each entry says whether it is one VISTA ships and recreates on a fresh start (a
  bundled skill, a seeded project or knowledge base) or the researcher's own
- **AND** a part it cannot read from that database's shape is left out instead of failing the
  refusal

#### Scenario: Running without the window

- **WHEN** a developer starts the backend directly on an incompatible database
- **THEN** the process exits non-zero, and its log names the database's path, the offending
  columns, and that moving or deleting the file starts fresh
