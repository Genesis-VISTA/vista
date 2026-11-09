"use client";

import { ThreadVerdict, Verdict } from "@/lib/debates";

/**
 * The thread's current verdict: its latest Referee's ruling, whoever's.
 *
 * Rendered separately from the thread because it is the thing a reader came
 * for — but it stays *next to* the argument rather than replacing it, so the
 * objections it weighed are one scroll away.
 */
export default function DebateVerdict({
  current,
  endedEarly,
}: {
  current: ThreadVerdict | null;
  /** The thread was closed; with no verdict, say so rather than show nothing. */
  endedEarly: boolean;
}) {
  if (endedEarly && !current) {
    return (
      <section className="debate-verdict debate-verdict--none">
        <h3>No verdict</h3>
        <p>
          This debate was ended before the Referee ruled, so there is no ranked
          hypothesis. What was argued is still in the thread below — a debate
          stopped early has no conclusion, and inventing one would report a
          result it never reached.
        </p>
      </section>
    );
  }

  if (!current) return null;
  // Another install's Referee wrote it: its words are that install's claim, so
  // say whose it is rather than present it as this lab's own ruling.
  const heading = current.ours ? "Verdict" : "Verdict · from another install";
  const verdict: Verdict | null = current.verdict;
  if (!verdict || verdict.ranked.length === 0) {
    // A DONE post with no structured verdict (an older install, or a peer):
    // the Referee's written ruling is still the verdict.
    return (
      <section className="debate-verdict">
        <h3>{heading}</h3>
        <p className="debate-verdict__rationale">{current.body}</p>
      </section>
    );
  }

  return (
    <section className="debate-verdict">
      <h3>{heading}</h3>
      <ol className="debate-verdict__list">
        {verdict.ranked.map((entry, index) => (
          <li key={index} className="debate-verdict__item">
            <p className="debate-verdict__claim">{entry.hypothesis.claim}</p>
            <p className="debate-verdict__mechanism">{entry.hypothesis.mechanism}</p>

            <dl className="debate-verdict__meta">
              <dt>Standing after the debate</dt>
              <dd>{entry.standing}</dd>
              <dt>Confidence</dt>
              <dd>{entry.hypothesis.confidence.toFixed(2)}</dd>
            </dl>

            {entry.hypothesis.predictions.length > 0 && (
              <>
                <p className="debate-verdict__label">
                  Falsifiable predictions
                </p>
                <ul>
                  {entry.hypothesis.predictions.map((prediction, i) => (
                    <li key={i}>{prediction}</li>
                  ))}
                </ul>
              </>
            )}

            {entry.hypothesis.open_risks.length > 0 && (
              <>
                <p className="debate-verdict__label">Open risks</p>
                <ul>
                  {entry.hypothesis.open_risks.map((risk, i) => (
                    <li key={i}>{risk}</li>
                  ))}
                </ul>
              </>
            )}
          </li>
        ))}
      </ol>

      <p className="debate-verdict__rationale">
        <strong>Why this order.</strong> {verdict.rationale}
      </p>

      {verdict.unresolved.length > 0 && (
        <div className="debate-verdict__unresolved">
          <p className="debate-verdict__label">
            Unresolved — what would settle it
          </p>
          <ul>
            {verdict.unresolved.map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
