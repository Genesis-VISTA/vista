## ADDED Requirements

### Requirement: Each debate role has its own model

Opening a debate SHALL let the researcher choose a model for each role (Proposer, Reviewer
and Referee) or leave a role on the default. A role left on the default SHALL use the
researcher's default model as it is when each run of the debate starts. Continuing a debate
SHALL offer the same choice, starting from the models the debate used last, and SHALL keep
them unless the researcher changes them there. A role's model MUST NOT change while a run
is in progress. Each role's model SHALL be a model name sent to the opener's provider. The
debate view SHALL show each role's model, and each post a role writes SHALL record locally
the model that wrote it.

#### Scenario: A stronger Referee

- **GIVEN** a researcher whose default is model A
- **WHEN** they open a debate with the Referee set to model B and the other roles left on
  the default
- **THEN** the Proposer and Reviewer argue with A, and the verdict is written by B

#### Scenario: Continue keeps the debate's models

- **GIVEN** a concluded debate whose Referee used model B
- **WHEN** the researcher continues it for 2 more rounds without changing the models
- **THEN** the Referee's posts in the new rounds are written by B

#### Scenario: Continue may change a role's model

- **GIVEN** a concluded debate whose Reviewer used model A
- **WHEN** the researcher continues it with the Reviewer set to model C
- **THEN** the Reviewer's posts in the new rounds are written by C, and its earlier posts
  still record A

#### Scenario: Changing the default between runs

- **GIVEN** a concluded debate whose Proposer was left on the default, which was A
- **WHEN** the researcher changes the default to B and continues the debate
- **THEN** the Continue form shows the Proposer on "Default (B)", and its new posts are
  written by B

#### Scenario: Each post shows its model

- **WHEN** a researcher views a debate whose roles used different models
- **THEN** the view names each role's model, and each role's post names the model that
  wrote it
