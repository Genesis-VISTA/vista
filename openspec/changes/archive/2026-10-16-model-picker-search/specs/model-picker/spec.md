## ADDED Requirements

### Requirement: The model list can be searched

When the provider lists at least one model, the picker SHALL open on a search box that
has focus. Typing SHALL narrow the list to the identifiers that contain the text,
ignoring case, keeping their sorted order. Enter SHALL choose the first model still
listed. When nothing matches, the picker SHALL say so. The search SHALL be cleared
when the picker closes.

#### Scenario: Narrowing a long list

- **WHEN** a researcher on AmSC MAG opens the picker and types part of a model's name
- **THEN** only the models whose identifiers contain that text, in any case, are listed

#### Scenario: Choosing with Enter

- **WHEN** a researcher types in the search box and presses Enter
- **THEN** the first model still listed becomes their active model and the picker closes

#### Scenario: Nothing matches

- **WHEN** the search text is in no listed identifier
- **THEN** the picker lists no models, says that none match, and still offers to use
  another model by name

#### Scenario: Reopening

- **WHEN** a researcher closes the picker after searching and opens it again
- **THEN** the search box is empty and every listed model is shown

### Requirement: Full identifiers on hover

Each listed model, and the picker's label, SHALL show the model's full identifier on
hover, so an identifier cut off for width can still be read.

#### Scenario: A long variant name

- **WHEN** a researcher hovers over a listed model whose identifier is too long for the
  menu
- **THEN** its full identifier is shown
