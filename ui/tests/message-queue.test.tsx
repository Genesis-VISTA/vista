import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import MessageQueue from "@/components/MessageQueue";
import {
  deleteQueued,
  editQueued,
  queuedMessages,
  readInboxChanged,
  readQueuedSend,
  restoreWithdrawn,
  type InboxItem,
} from "@/lib/message-queue";

const item = (id: number, text: string, kind = "message"): InboxItem => ({
  id,
  kind,
  text,
  state: "queued",
  created_at: "2026-11-12T10:00:00Z",
});

describe("MessageQueue", () => {
  it("shows each queued message as a chip, and nothing when the queue is empty", () => {
    const { rerender, container } = render(
      <MessageQueue items={[item(1, "plot it"), item(2, "and save it")]} onEdit={vi.fn()} onDelete={vi.fn()} />,
    );
    expect(screen.getAllByText("Queued")).toHaveLength(2);
    expect(screen.getByText("plot it")).toBeInTheDocument();
    expect(screen.getByText("and save it")).toBeInTheDocument();

    rerender(<MessageQueue items={[]} onEdit={vi.fn()} onDelete={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("edits a queued message in place", async () => {
    const onEdit = vi.fn().mockResolvedValue(undefined);
    render(<MessageQueue items={[item(7, "plot it")]} onEdit={onEdit} onDelete={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit queued message" }));
    const box = screen.getByRole("textbox", { name: "Edit queued message" });
    await userEvent.clear(box);
    await userEvent.type(box, "plot it in log scale");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onEdit).toHaveBeenCalledWith(7, "plot it in log scale");
    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Edit queued message" })).not.toBeInTheDocument(),
    );
  });

  it("cancels an edit without saving", async () => {
    const onEdit = vi.fn();
    render(<MessageQueue items={[item(7, "plot it")]} onEdit={onEdit} onDelete={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Edit queued message" }));
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.getByText("plot it")).toBeInTheDocument();
  });

  it("deletes a queued message", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<MessageQueue items={[item(3, "never mind")]} onEdit={vi.fn()} onDelete={onDelete} />);

    await userEvent.click(screen.getByRole("button", { name: "Delete queued message" }));

    expect(onDelete).toHaveBeenCalledWith(3);
  });
});

describe("reading the queue", () => {
  it("takes a 202 with started false as a queued send", () => {
    const queued = item(4, "again");
    expect(readQueuedSend(202, { started: false, item: queued })).toEqual(queued);
    expect(readQueuedSend(200, { started: false, item: queued })).toBeNull();
    expect(readQueuedSend(202, { started: false })).toBeNull();
    expect(readQueuedSend(202, null)).toBeNull();
  });

  it("shows only the researcher's own queued messages as chips", () => {
    const items = [item(1, "hi"), item(2, "bg-1 exited", "task_event"), { ...item(3, "x"), state: "taken" }];
    expect(queuedMessages(items).map((i) => i.id)).toEqual([1]);
  });

  it("reads an inbox_changed event, dropping anything malformed", () => {
    expect(readInboxChanged({ queued: [item(1, "a"), { id: "x" }] })).toEqual([item(1, "a")]);
    expect(readInboxChanged(null)).toEqual([]);
  });

  it("puts withdrawn messages back in the box ahead of what was typed since", () => {
    expect(restoreWithdrawn("", ["plot it", "save it"])).toBe("plot it\n\nsave it");
    expect(restoreWithdrawn("and label the axes", ["plot it"])).toBe(
      "plot it\n\nand label the axes",
    );
  });
});

describe("changing a queued message", () => {
  it("reports a message the agent already took, with what it received", async () => {
    const taken = { ...item(5, "plot it"), state: "taken" };
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ code: "inbox_item_taken", detail: "too late", item: taken }), {
        status: 409,
        headers: { "content-type": "application/json" },
      }),
    );

    expect(await editQueued("p", "c", 5, "changed")).toEqual({ ok: false, taken: true, item: taken });
  });

  it("sends an edit and a delete to the inbox route", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify(item(5, "new")), { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    expect(await editQueued("my project", "c1", 5, "new")).toEqual({ ok: true, item: item(5, "new") });
    expect(await deleteQueued("my project", "c1", 5)).toEqual({ ok: true, item: null });

    const [editUrl, editInit] = fetchMock.mock.calls[0];
    expect(editUrl).toBe("/api/chat/inbox?project_name=my+project&chat_session_id=c1&item_id=5");
    expect(editInit).toMatchObject({ method: "PATCH", body: JSON.stringify({ text: "new" }) });
    expect(fetchMock.mock.calls[1][1]).toMatchObject({ method: "DELETE" });
  });
});
