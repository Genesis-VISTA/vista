import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, isToolUIPart } from "ai";
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

const transport = new DefaultChatTransport({
  api: "http://localhost:8000/chat",
});

export default function App() {
  const { messages, sendMessage, status, stop } = useChat({ transport });

  return (
    <div className="flex h-screen flex-col mx-auto max-w-3xl">
      <Conversation className="flex-1">
        <ConversationContent>
          {messages.length === 0 ? (
            <ConversationEmptyState
              title="Vista Chat"
              description="Send a message to start a conversation"
            />
          ) : (
            messages.map((message) => (
              <Message key={message.id} from={message.role}>
                <MessageContent>
                  {message.parts?.map((part, i) => {
                    if (part.type === "text") {
                      return message.role === "assistant" ? (
                        <MessageResponse key={i}>{part.text}</MessageResponse>
                      ) : (
                        <span key={i}>{part.text}</span>
                      );
                    }
                    if (isToolUIPart(part)) {
                      return (
                        <Tool key={i}>
                          <ToolHeader
                            {...(part.type === "dynamic-tool"
                              ? { type: part.type, state: part.state, toolName: part.toolName }
                              : { type: part.type, state: part.state })}
                          />
                          <ToolContent>
                            <ToolInput
                              input={JSON.stringify(part.input, null, 2)}
                            />
                            {part.state === "output-available" && (
                              <ToolOutput
                                output={JSON.stringify(part.output, null, 2)}
                                errorText={part.errorText}
                              />
                            )}
                          </ToolContent>
                        </Tool>
                      );
                    }
                    return null;
                  })}
                </MessageContent>
              </Message>
            ))
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
  );
}
