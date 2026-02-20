import React, { useEffect, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, isToolUIPart, type FileUIPart } from "ai";
import { FilePanel } from "@/components/file-panel";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message,
  MessageContent,
  MessageResponse,
} from "@/components/ai-elements/message";
import {
  PromptInput,
  PromptInputTextarea,
  PromptInputSubmit,
} from "@/components/ai-elements/prompt-input";
import {
  Tool,
  ToolHeader,
  ToolContent,
  ToolInput,
  ToolOutput,
} from "@/components/ai-elements/tool";
import { McpApp } from "@/components/mcp-app";

const transport = new DefaultChatTransport({api: `${BACKEND_URL}/chat`});

export default function App() {
  const { messages, sendMessage, status, stop } = useChat({ transport });
  const [toolResourceUris, setToolResourceUris] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch(`${BACKEND_URL}/mcp/tools`)
      .then((r) => r.json())
      .then((tools: any[]) => {
        const map: Record<string, string> = {};
        for (const tool of tools) {
          map[tool.name] = tool._meta?.ui?.resourceUri;
        }
        setToolResourceUris(map);
      })
  }, []);

  return (
    <div className="flex h-screen">
      <FilePanel />
      <div className="flex flex-1 flex-col mx-auto max-w-3xl">
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.length === 0 ? (
              <ConversationEmptyState
                title="Vista Chat"
                description="Send a message to start a conversation"
              />
            ) : (
              messages.map((message) => {
                // Associate { type: "file" } parts with the tool part that immediately precedes them.
                // PydanticAI sends binary results as FileChunk SSE events that arrive as file parts
                // in the same assistant message, right after the tool-output part.
                // See notes in backend/src/vista_backend/agent.py:_pack_binary_results for how this
                // hack works.
                const filesByToolIndex = new Map<number, Array<FileUIPart>>();
                let lastToolIdx = -1;
                message.parts?.forEach((part, i) => {
                  if (isToolUIPart(part)) {
                    lastToolIdx = i;
                  } else if (part.type === "file" && lastToolIdx >= 0) {
                    const files = filesByToolIndex.get(lastToolIdx) ?? [];
                    files.push(part);
                    filesByToolIndex.set(lastToolIdx, files);
                  } else {
                    lastToolIdx = -1;
                  }
                });

                return (
                  <Message key={message.id} from={message.role}>
                    <MessageContent>
                      {message.parts?.map((part, i) => {
                        if (part.type === "text") {
                          return message.role === "assistant" ? (
                            <MessageResponse key={i}>{part.text}</MessageResponse>
                          ) : (
                            <span key={i}>{part.text}</span>
                          );
                        } else if (isToolUIPart(part)) {
                          const toolName = part.type === "dynamic-tool" ? part.toolName : part.type.replace(/^tool-/, '');
                          const resourceUri = toolName ? toolResourceUris[toolName] : undefined;
                          const hasMcpApp = resourceUri && part.state == "output-available";
                          return (
                            <React.Fragment key={i}>
                              <Tool key={i}>
                                {part.type === "dynamic-tool" ? (
                                  <ToolHeader type={part.type} state={part.state} toolName={part.toolName}/>
                                ) : (
                                  <ToolHeader type={part.type} state={part.state}/>
                                )}
                                <ToolContent>
                                  <ToolInput input={JSON.stringify(part.input, null, 2)}/>
                                  {part.state === "output-available" && (
                                    <ToolOutput
                                      output={JSON.stringify(part.output, null, 2)}
                                      errorText={part.errorText}
                                    />
                                  )}
                                </ToolContent>
                              </Tool>
                              {hasMcpApp && (
                                <McpApp
                                  toolName={toolName}
                                  toolInput={part.input ?? {} as any}
                                  toolOutput={part.output}
                                  toolFiles={filesByToolIndex.get(i)}
                                  resourceUri={resourceUri}
                                />
                              )}
                            </React.Fragment>
                          );
                        }
                      })}
                    </MessageContent>
                  </Message>
                );
              })
            )}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>

        <div className="border-t p-4">
          <PromptInput
            onSubmit={(message) => {
              if (message.text.trim()) {
                sendMessage({ text: message.text });
              }
            }}
          >
            <PromptInputTextarea placeholder="Type your message..." />
            <PromptInputSubmit status={status} onStop={stop} />
          </PromptInput>
        </div>
      </div>
    </div>
  );
}
