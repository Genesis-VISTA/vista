/** Rows a table preview shows before it says how many it left out. */
export const CSV_PREVIEW_ROWS = 500;

export type CsvPreview = {
  rows: string[][];
  omitted: number;
};

/**
 * Parse delimited text for a preview: quoted fields (embedded delimiters,
 * newlines and doubled quotes), CRLF or LF. Returns the first `maxRows` rows
 * and how many more there were. A trailing newline does not add an empty row.
 */
export function parseDelimited(text: string, delimiter: string = ",", maxRows: number = CSV_PREVIEW_ROWS): CsvPreview {
  const rows: string[][] = [];
  let total = 0;
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  let pending = false; // something has been read for the current row

  const endRow = () => {
    row.push(field);
    if (total < maxRows) rows.push(row);
    total += 1;
    row = [];
    field = "";
    pending = false;
  };

  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += c;
      }
      continue;
    }
    if (c === '"' && field === "") {
      inQuotes = true;
      pending = true;
    } else if (c === delimiter) {
      row.push(field);
      field = "";
      pending = true;
    } else if (c === "\r" || c === "\n") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      endRow();
    } else {
      field += c;
      pending = true;
    }
  }
  if (pending || field !== "") endRow();
  return { rows, omitted: total - rows.length };
}

/** `.tsv` is tab separated; everything else is comma separated. */
export function delimiterFor(name: string): string {
  return name.toLowerCase().endsWith(".tsv") ? "\t" : ",";
}
