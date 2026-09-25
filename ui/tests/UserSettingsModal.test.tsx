import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UserSettingsModal } from "@/components/UserSettingsModal";
import type { UserPublicWithConfig } from "@/lib/user";

const { fetchCurrentUserWithConfigMock, updateCurrentUserMock } = vi.hoisted(() => ({
  fetchCurrentUserWithConfigMock: vi.fn(),
  updateCurrentUserMock: vi.fn(),
}));
vi.mock("@/lib/user", () => ({
  fetchCurrentUserWithConfig: fetchCurrentUserWithConfigMock,
  updateCurrentUser: updateCurrentUserMock,
  startGlobusLogin: vi.fn(),
  completeGlobusLogin: vi.fn(),
}));

function user(overrides: Partial<UserPublicWithConfig> = {}): UserPublicWithConfig {
  return {
    id: "u1",
    email: "researcher@ornl.gov",
    is_admin: false,
    inference_model: null,
    inference_base_url: null,
    inference_api_key: null,
    nersc_account: null,
    nersc_remote_dir: null,
    odo_s3m_token: null,
    frontier_s3m_token: null,
    nersc_iri_token: null,
    globus_token: null,
    odo_globus_token: null,
    frontier_globus_token: null,
    globus_https_token: null,
    odo_globus_https_token: null,
    frontier_globus_https_token: null,
    ...overrides,
  };
}

beforeEach(() => {
  fetchCurrentUserWithConfigMock.mockReset();
  updateCurrentUserMock.mockReset();
  updateCurrentUserMock.mockResolvedValue(user());
});

describe("UserSettingsModal S3M tokens", () => {
  it("has a separate token field for each OLCF cluster", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ odo_s3m_token: "odo-tok", frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} />);

    expect(await screen.findByLabelText(/Odo S3M token/)).toHaveValue("odo-tok");
    expect(screen.getByLabelText(/Frontier S3M token/)).toHaveValue("fr-tok");
  });

  it("saving one cluster's token sends only that field", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} />);

    await userEvent.type(await screen.findByLabelText(/Odo S3M token/), "new-odo");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledTimes(1);
    expect(updateCurrentUserMock).toHaveBeenCalledWith({ odo_s3m_token: "new-odo" });
  });

  it("clearing one token sends null for it alone", async () => {
    fetchCurrentUserWithConfigMock.mockResolvedValue(
      user({ odo_s3m_token: "odo-tok", frontier_s3m_token: "fr-tok" }),
    );
    render(<UserSettingsModal onClose={() => {}} />);

    await userEvent.clear(await screen.findByLabelText(/Frontier S3M token/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(updateCurrentUserMock).toHaveBeenCalledWith({ frontier_s3m_token: null });
  });
});
