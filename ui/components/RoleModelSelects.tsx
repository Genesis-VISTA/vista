"use client";

import { ModelMenu, useProviderModels } from "@/components/ModelMenu";
import { DEBATE_ROLES, type RoleModels } from "@/lib/debates";
import { useAgentSettings } from "@/lib/agent-settings";

/**
 * A model for each debate role, on the open-debate form and on Continue.
 *
 * Three of the chat picker's menus, each starting at "Default (<model>)": a
 * role left there argues on the researcher's default as it is when the run
 * starts. Only the model changes per role; provider, endpoint and key stay the
 * researcher's.
 */
export function RoleModelSelects({
  value,
  onChange,
}: {
  value: RoleModels;
  onChange: (next: RoleModels) => void;
}) {
  const { settings } = useAgentSettings();
  // One list for the three menus: each fetching its own cost a provider call per role.
  const models = useProviderModels();
  return (
    <div className="debate-role-models">
      {DEBATE_ROLES.map((role) => (
        <div key={role} className="debate-role-model">
          <span className="debate-role-model__name">{role}</span>
          <ModelMenu
            name={`${role[0].toUpperCase()}${role.slice(1)} model`}
            value={value[role]}
            defaultModel={settings?.model ?? null}
            offerDefault
            models={models}
            onChange={async (model) => onChange({ ...value, [role]: model })}
          />
        </div>
      ))}
    </div>
  );
}
