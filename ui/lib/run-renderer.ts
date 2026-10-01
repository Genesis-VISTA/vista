/**
 * Drawing a chat run from its events.
 *
 * One renderer serves every way a run reaches the page: the stream a send opens,
 * the stream a re-attach opens, and the events stored for a turn nobody
 * watched. They are the same events from the same start, so the page cannot tell
 * them apart, and a turn drawn after the fact looks like one drawn live.
 *
 * The renderer knows nothing about React. It calls a `RunHost` that applies each
 * change to the state of one conversation, and keeps only what it needs between
 * events (the streaming parts, the id of the run it is drawing).
 *
 * Every bubble it creates carries the run's `run_id`. Drawing a run again (after
 * navigating away and back) first removes that run's earlier bubbles, so a
 * partially saved thread is redrawn rather than duplicated.
 */

import type { DecisionMetadata } from "@/components/ToolApprovalModal";
import {
  fileFromToolReturnContent,
  htmlFromToolReturnContent,
  textFromToolReturnContent,
  type FunctionToolCallEvent,
  type FunctionToolResultEvent,
  type LogEvent,
  type McpFormElicitationEvent,
  type McpToolApprovalEvent,
  type McpUrlElicitationEvent,
  type PartDeltaEvent,
  type PartEndEvent,
  type PartStartEvent,
} from "./agent-events";
import { labelForTool } from "./tool-labels";
import type { ChatMessage, ExecutionResult } from "./types";

export type RunFinishState = "done" | "failed" | "interrupted" | "stopped";

/** Something the run is waiting on the researcher for. */
export type RunPrompt =
  | { kind: "form"; id: string; message: string; schema: Record<string, unknown> }
  | {
      kind: "approval";
      id: string;
      toolName: string;
      message: string;
      args: Record<string, unknown> | null;
      decisionMetadata: DecisionMetadata | null;
    }
  | { kind: "url"; id: string; message: string; url: string };

/** What the page does in response, for the conversation being drawn. */
export interface RunHost {
  setMessages(update: (prev: ChatMessage[]) => ChatMessage[]): void;
  setLatestIntermediateId(update: (prev: string | null) => string | null): void;
  setLiveStatus(status: string | null): void;
  setLatestResult(update: (prev: ExecutionResult | null) => ExecutionResult | null): void;
  pushLog(level: string, area: string, message: string): void;
  /** Show a prompt. The page routes it to the approval modal, the form modal, or a confirm. */
  prompt(prompt: RunPrompt): void;
  /** Another view answered: stop showing the prompt with this id. */
  promptResolved(id: string): void;
  scrollToLatest(): void;
}

export type RunRendererOptions = {
  /**
   * The bubble the page drew for the prompt it is sending. It carries the
   * run's id once `run_started` says what that is.
   */
  userBubbleId?: string;
  /**
   * Draw the researcher's question from `run_started`. For a re-attach or a
   * replay, where the page did not draw it itself.
   */
  drawUserPrompt?: boolean;
  newId?: () => string;
};

export const FINISH_NOTES: Record<Exclude<RunFinishState, "done">, string> = {
  stopped: "Stopped",
  interrupted: "Interrupted when VISTA quit",
  failed: "Agent run failed",
};

type PartAcc =
  | { kind: "text"; bubbleId: string; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool-call"; toolName: string; argsText: string };

export type RunRenderer = {
  dispatch(eventName: string | null, data: unknown): void;
  /** The run being drawn, once `run_started` has arrived. */
  readonly runId: string | null;
  /** How the run ended, once `run_finished` has arrived. */
  readonly finishedState: RunFinishState | null;
  /** Whether a run result arrived, which older streams used instead of `run_finished`. */
  readonly sawResult: boolean;
};

export function createRunRenderer(host: RunHost, options: RunRendererOptions = {}): RunRenderer {
  const newId = options.newId ?? (() => crypto.randomUUID());
  // Per-turn streaming-part accumulator, keyed by PartStartEvent.index.
  const parts = new Map<number, PartAcc>();
  let runId: string | null = null;
  let finishedState: RunFinishState | null = null;
  let sawResult = false;
  let finalSignaled = false;
  let liveAssistantBubbleId: string | null = null;

  const tagged = (message: ChatMessage): ChatMessage =>
    runId ? { ...message, run_id: runId } : message;

  const updateBubble = (bubbleId: string, patch: Partial<ChatMessage>) =>
    host.setMessages((prev) => prev.map((m) => (m.id === bubbleId ? { ...m, ...patch } : m)));

  function dispatch(eventName: string | null, data: unknown) {
    const kind =
      eventName ??
      (data && typeof data === "object"
        ? ((data as { event_kind?: string }).event_kind ?? null)
        : null);

    switch (kind) {
      case "run_started": {
        const ev = data as { run_id?: string; user_prompt?: string };
        if (typeof ev.run_id !== "string") break;
        runId = ev.run_id;
        const id = ev.run_id;
        if (options.drawUserPrompt) {
          const prompt = ev.user_prompt ?? "";
          host.setMessages((prev) => {
            // Redraw this run from scratch. The page may already hold bubbles
            // from it, saved while it was being watched before.
            const kept = prev.filter((m) => m.run_id !== id);
            // A send that died between drawing the question and learning the
            // run's id leaves the question behind without a tag.
            const last = kept[kept.length - 1];
            if (last && last.role === "user" && !last.run_id && last.content === prompt) {
              kept.pop();
            }
            return [...kept, { id: newId(), role: "user", content: prompt, run_id: id }];
          });
        } else if (options.userBubbleId) {
          updateBubble(options.userBubbleId, { run_id: id });
        }
        break;
      }

      case "log": {
        const ev = data as LogEvent;
        host.pushLog(ev.level, ev.area, ev.message);
        break;
      }

      case "part_start": {
        const ev = data as PartStartEvent;
        const part = ev.part;
        if (!part || typeof part !== "object") break;
        const pkind = (part as { part_kind?: string }).part_kind;
        if (pkind === "text") {
          const initial = ((part as { content?: unknown }).content as string) ?? "";
          const bubbleId = newId();
          const intermediate = !finalSignaled;
          host.setMessages((prev) => [
            ...prev,
            tagged({ id: bubbleId, role: "assistant", content: initial, intermediate }),
          ]);
          if (intermediate) {
            host.setLatestIntermediateId(() => bubbleId);
            host.scrollToLatest();
          }
          liveAssistantBubbleId = bubbleId;
          parts.set(ev.index, { kind: "text", bubbleId, text: initial });
        } else if (pkind === "thinking") {
          const initial = ((part as { content?: unknown }).content as string) ?? "";
          parts.set(ev.index, { kind: "thinking", text: initial });
          if (initial) host.pushLog("INFO", "Agent", `thinking: ${initial}`);
        } else if (pkind === "tool-call" || pkind === "builtin-tool-call") {
          const toolName = ((part as { tool_name?: unknown }).tool_name as string) ?? "";
          parts.set(ev.index, { kind: "tool-call", toolName, argsText: "" });
        }
        break;
      }

      case "part_delta": {
        const ev = data as PartDeltaEvent;
        const acc = parts.get(ev.index);
        if (!acc) break;
        const delta = ev.delta;
        if (!delta || typeof delta !== "object") break;
        const dkind = (delta as { part_delta_kind?: string }).part_delta_kind;
        if (dkind === "text" && acc.kind === "text") {
          const chunk = ((delta as { content_delta?: unknown }).content_delta as string) ?? "";
          if (chunk) {
            acc.text += chunk;
            updateBubble(acc.bubbleId, { content: acc.text });
          }
        } else if (dkind === "thinking" && acc.kind === "thinking") {
          const chunk = ((delta as { content_delta?: unknown }).content_delta as string) ?? "";
          if (chunk) acc.text += chunk;
        } else if (dkind === "tool_call" && acc.kind === "tool-call") {
          const nameChunk =
            ((delta as { tool_name_delta?: unknown }).tool_name_delta as string) ?? "";
          const argsChunk = (delta as { args_delta?: unknown }).args_delta;
          if (nameChunk) acc.toolName += nameChunk;
          if (typeof argsChunk === "string") acc.argsText += argsChunk;
        }
        break;
      }

      case "part_end": {
        const ev = data as PartEndEvent;
        const acc = parts.get(ev.index);
        const part = ev.part;
        if (acc?.kind === "text" && part && typeof part === "object") {
          const final = ((part as { content?: unknown }).content as string) ?? acc.text;
          updateBubble(acc.bubbleId, { content: final });
        } else if (acc?.kind === "thinking" && acc.text) {
          host.pushLog("INFO", "Agent", `thinking: ${acc.text}`);
        }
        parts.delete(ev.index);
        break;
      }

      case "final_result": {
        finalSignaled = true;
        // If a text bubble is already streaming, this turn's text part IS the
        // final answer: promote it now so it doesn't get collapsed.
        if (liveAssistantBubbleId) {
          const bubbleId = liveAssistantBubbleId;
          updateBubble(bubbleId, { intermediate: false });
          host.setLatestIntermediateId((prev) => (prev === bubbleId ? null : prev));
        }
        break;
      }

      case "function_tool_call": {
        const ev = data as FunctionToolCallEvent;
        const toolName = ev.part?.tool_name ?? "tool";
        const newBubbleId = newId();
        // Still recorded as a step: `messages` is what gets persisted, so it is
        // also what the Activity tab can show after a reload.
        host.setMessages((prev) => [
          ...prev,
          tagged({
            id: newBubbleId,
            role: "assistant",
            content: `Calling \`${toolName}\`…`,
            intermediate: true,
          }),
        ]);
        host.setLatestIntermediateId(() => newBubbleId);
        host.setLiveStatus(labelForTool(toolName));
        break;
      }

      case "function_tool_result": {
        const ev = data as FunctionToolResultEvent;
        // PydanticAI 1.105 renamed `result` -> `part`; fall back to `result`
        // for older backends.
        const result = ev.part ?? ev.result;
        if (!result) break;
        if (result.part_kind === "retry-prompt") {
          host.pushLog("WARNING", `Tool:${result.tool_name ?? "unknown"}`, `Tool failed`);
          break;
        }
        host.setLiveStatus("Working on it");
        if (result.part_kind === "tool-return" || result.part_kind === "builtin-tool-return") {
          const file =
            result.tool_name === "display_file"
              ? fileFromToolReturnContent(result.content)
              : null;
          const html = file ? null : htmlFromToolReturnContent(result.content);
          const ui = file
            ? ({ kind: "file", url: file.url, mimeType: file.mimeType, name: file.name } as const)
            : html
              ? ({ kind: "html", html } as const)
              : null;
          // The text matters even when there is nothing to render: it is what
          // the prediction summary and references panels read.
          const stdout = file || html ? "" : (textFromToolReturnContent(result.content) ?? "");
          if (ui || stdout) {
            host.setLatestResult((prev) => ({
              ok: true,
              // Keep text from an earlier tool in the same turn when this one
              // only produced a figure, so a run that plots *and* reports does
              // not lose half of itself.
              stdout: stdout || prev?.stdout || "",
              stderr: "",
              artifacts: [],
              meta: { tool: result.tool_name },
              ui: ui ?? prev?.ui,
            }));
          }
        }
        break;
      }

      case "agent_run_result": {
        sawResult = true;
        host.setLiveStatus(null);
        if (liveAssistantBubbleId) updateBubble(liveAssistantBubbleId, { intermediate: false });
        host.setLatestIntermediateId(() => null);
        break;
      }

      case "run_finished": {
        const state = (data as { state?: string }).state;
        if (state !== "done" && state !== "failed" && state !== "interrupted" && state !== "stopped") {
          break;
        }
        finishedState = state;
        host.setLiveStatus(null);
        host.setLatestIntermediateId(() => null);
        if (state !== "done") {
          host.setMessages((prev) => [
            ...prev,
            tagged({ id: newId(), role: "system", content: FINISH_NOTES[state] }),
          ]);
        }
        break;
      }

      case "mcp_form_elicitation": {
        const ev = data as McpFormElicitationEvent;
        host.prompt({
          kind: "form",
          id: ev.elicitation_id,
          message: ev.message,
          schema: ev.requested_schema,
        });
        break;
      }

      case "mcp_url_elicitation": {
        const ev = data as McpUrlElicitationEvent;
        host.prompt({ kind: "url", id: ev.elicitation_id, message: ev.message, url: ev.url });
        break;
      }

      case "mcp_tool_approval": {
        const ev = data as McpToolApprovalEvent;
        host.prompt({
          kind: "approval",
          id: ev.elicitation_id,
          toolName: ev.tool_name,
          message: ev.message,
          args: ev.args ?? null,
          decisionMetadata: (ev.decision_metadata as DecisionMetadata) ?? null,
        });
        break;
      }

      case "prompt_resolved": {
        const id = (data as { elicitation_id?: string }).elicitation_id;
        if (typeof id === "string") host.promptResolved(id);
        break;
      }
    }
  }

  return {
    dispatch,
    get runId() {
      return runId;
    },
    get finishedState() {
      return finishedState;
    },
    get sawResult() {
      return sawResult;
    },
  };
}
