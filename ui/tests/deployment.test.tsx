import { render, renderHook, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { UpdateControls } from "@/components/UpdateControls";
import { clusterAllowed, deploymentOf, useDeployment } from "@/lib/deployment";
import { HPC_CLUSTERS } from "@/lib/hpc-status";
import type { VersionInfo } from "@/lib/version";

const { useVersionMock } = vi.hoisted(() => ({ useVersionMock: vi.fn() }));
vi.mock("@/lib/version", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/version")>()),
  useVersion: useVersionMock,
}));

function view(info: Partial<VersionInfo> | null) {
  return {
    info: info && {
      version: "0.2.3",
      commit: null,
      checks: "on" as const,
      checked_at: null,
      latest: null,
      notes_url: null,
      data_reset: null,
      ...info,
    },
    error: null,
    checking: false,
    checkedAt: 0,
    download: null,
  };
}

beforeEach(() => {
  useVersionMock.mockReturnValue(view({}));
});

describe("allowed clusters", () => {
  it("lists only the clusters a hosted backend reports", () => {
    const hosted = deploymentOf({ deployment: "hosted", allowed_clusters: ["odo"] });
    expect(HPC_CLUSTERS.filter((c) => clusterAllowed(hosted, c))).toEqual(["odo"]);
  });

  it("allows every cluster when the backend says nothing", () => {
    const desktop = deploymentOf({ deployment: "desktop" });
    expect(HPC_CLUSTERS.filter((c) => clusterAllowed(desktop, c))).toEqual([...HPC_CLUSTERS]);
    expect(clusterAllowed(deploymentOf(null), "frontier")).toBe(true);
  });
});

describe("deploymentOf", () => {
  it("reads hosted and desktop from the version answer", () => {
    expect(deploymentOf({ deployment: "hosted", sign_out_url: "https://gw/logout" })).toMatchObject({
      deployment: "hosted",
      hosted: true,
      signOutUrl: "https://gw/logout",
    });
    expect(deploymentOf({ deployment: "desktop", sign_out_url: "https://gw/logout" })).toMatchObject({
      deployment: "desktop",
      hosted: false,
      signOutUrl: null,
    });
  });

  it("is desktop until the backend has answered, or when it predates the flag", () => {
    expect(deploymentOf(null).hosted).toBe(false);
    expect(deploymentOf({}).hosted).toBe(false);
  });
});

describe("useDeployment", () => {
  it.each(["desktop", "hosted"] as const)("follows the backend: %s", (deployment) => {
    useVersionMock.mockReturnValue(view({ deployment }));
    expect(renderHook(() => useDeployment()).result.current.deployment).toBe(deployment);
  });
});

describe("UpdateControls", () => {
  it.each([
    ["desktop", true],
    ["hosted", false],
  ] as const)("on %s, Check now is shown: %s", (deployment, shown) => {
    const v = view({ deployment });
    useVersionMock.mockReturnValue(v);
    render(<UpdateControls info={v.info as VersionInfo} />);
    expect(screen.queryByRole("button", { name: "Check now" }) !== null).toBe(shown);
  });
});
