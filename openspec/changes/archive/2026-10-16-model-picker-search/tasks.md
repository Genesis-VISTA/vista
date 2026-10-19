## 1. Picker

- [x] 1.1 Add a search box to the `ModelPicker` menu, shown when the provider lists at least one model and focused when the menu opens; filter the sorted list by case-insensitive substring, say when nothing matches, choose the first match on Enter, and clear the search when the menu closes. Verify with `ModelPicker.test.tsx` tests for filtering, Enter, the no-match message and clearing on close.
- [x] 1.2 Give each option and the picker's label a `title` with the full identifier. Verify with a `ModelPicker.test.tsx` test using a long identifier.
- [x] 1.3 Keep the search box pinned at the top of the scrolling menu (`.model-picker-search` in `globals.css`), and run `./scripts/ci-local.sh`.
