import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
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
                  {message.role === "assistant" ? (
                    <MessageResponse>
                      {message.parts
                        ?.filter((part) => part.type === "text")
                        .map((part) => part.text)
                        .join("")}
                    </MessageResponse>
                  ) : (
                    message.parts
                      ?.filter((part) => part.type === "text")
                      .map((part) => part.text)
                      .join("")
                  )}
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
