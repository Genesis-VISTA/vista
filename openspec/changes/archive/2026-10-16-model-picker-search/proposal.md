## Why

AmSC MAG lists many models, with long, similar identifiers that differ only in a
variant suffix. In the chat-header picker a researcher has to scroll the whole list to
find one, and a long identifier is cut off at the menu's width, so two variants can
look the same.

## What Changes

- The picker opens on a search box that narrows the list as the researcher types.
  Enter chooses the first match.
- Each listed model, and the picker's label, show the full identifier on hover.

## Capabilities

### New Capabilities
(none)

### Modified Capabilities
- `model-picker`: the list can be searched, and identifiers cut off for width can be
  read in full.

## Impact

- Frontend only: `ui/components/ModelPicker.tsx`, its styles in `ui/app/globals.css`,
  and `ui/tests/ModelPicker.test.tsx`. No backend or API change.
