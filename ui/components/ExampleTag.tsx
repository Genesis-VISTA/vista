/** Marks one of the tour's examples, so it is never taken for real work. */
export function ExampleTag() {
  return (
    <span className="tour-example-tag" title="An example for the tour: it does nothing">
      Example
    </span>
  );
}

export default ExampleTag;
