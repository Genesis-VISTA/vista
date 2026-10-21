"use client";

import { useEffect, useRef } from "react";
import { ModelMenu } from "@/components/ModelMenu";
import { useActiveProject } from "@/lib/projects";
import { savePersistedChatSession } from "@/lib/chat-session";
import { refreshAgentSettings, useAgentSettings } from "@/lib/agent-settings";
import { updateCurrentUser } from "@/lib/user";

/** The open chat, as the picker needs it: which one, its own model, and who hears of a change. */
export type ChatModelBinding = {
  id: string;
  /** The chat's own model (`openai:<name>`); null follows the researcher's default. */
  model: string | null;
  /** Called with the chat's model after it is saved. */
  onModelChange: (model: string | null) => void;
};

/**
 * The model crumb in the chat header, next to the project switcher, and the
 * one place a researcher chooses a model.
 *
 * Two wrappers over `ModelMenu`, told apart by `chat`:
 * - No chat open (the conversation list): the menu is the researcher's model.
 *   Selecting writes `inference_model` and refreshes the shared agent-settings
 *   store, so the label and Settings never disagree.
 * - A chat open: the menu is that chat's model. Selecting patches the chat and
 *   leaves the researcher's default alone; the label adds "· this chat" while
 *   the chat has a model of its own, and "Use default (<model>)" returns it to
 *   following the default.
 *
 * Both read the store rather than a copy of their own.
 */
export function ModelPicker({ chat }: { chat?: ChatModelBinding }) {
  return chat ? <ChatModelPicker chat={chat} /> : <DefaultModelPicker />;
}

function DefaultModelPicker() {
  const { settings } = useAgentSettings();
  return (
    <ModelMenu
      value={settings && !settings.modelIsDefault ? settings.model : null}
      defaultModel={settings?.modelIsDefault ? settings.model : null}
      onChange={async (model) => {
        // The default has no "use default" to return to: only a model is chosen here.
        if (model === null) return;
        await updateCurrentUser({ inference_model: model });
        await refreshAgentSettings();
      }}
    />
  );
}

function ChatModelPicker({ chat }: { chat: ChatModelBinding }) {
  const activeProject = useActiveProject();
  const { settings } = useAgentSettings();
  // The chat open now, or null once this picker is gone. A save that returns
  // after another chat opened must not report its model as that chat's.
  const openChat = useRef<string | null>(chat.id);
  useEffect(() => {
    openChat.current = chat.id;
    return () => {
      openChat.current = null;
    };
  }, [chat.id]);
  return (
    <ModelMenu
      value={chat.model}
      // What a chat that follows the default uses: the researcher's model in effect.
      defaultModel={settings?.model ?? null}
      scope="this chat"
      offerDefault
      onChange={async (model) => {
        if (!activeProject) throw new Error("No active project.");
        const saved = await savePersistedChatSession(activeProject.name, {
          chatSessionId: chat.id,
          inferenceModel: model,
        });
        if (openChat.current !== chat.id) return;
        chat.onModelChange(saved.inference_model ?? null);
      }}
    />
  );
}

export default ModelPicker;
