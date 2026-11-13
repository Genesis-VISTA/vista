import { describe, expect, it } from "vitest";
import {
  filledNote,
  isFilledRemoteDir,
  projectFolder,
  remoteDirFill,
  suggestedRemoteDir,
} from "@/lib/remote-dir";

const ODO_GEN150 = "/gpfs/wolf2/olcf/gen150/proj-shared/vista";
const ODO_STF218 = "/gpfs/wolf2/olcf/stf218/proj-shared/vista";

describe("projectFolder", () => {
  it.each([
    ["gen150-vista", "gen150"],
    ["CHM243", "chm243"],
    ["stf218", "stf218"],
    ["", null],
    ["-x", null],
    [null, null],
    ["gen 150", null],
  ])("%j → %j", (project, folder) => {
    expect(projectFolder(project)).toBe(folder);
  });
});

describe("suggestedRemoteDir", () => {
  it.each([
    ["odo", "gen150-vista", ODO_GEN150],
    ["frontier", "chm243", "/lustre/orion/chm243/proj-shared/vista"],
    ["lux", "stf218", "/lustre/orion/stf218/proj-shared/vista"],
    ["perlmutter", "m1234", null],
    ["odo", "-x", null],
  ] as const)("%s from %j → %j", (cluster, project, dir) => {
    expect(suggestedRemoteDir(cluster, project)).toBe(dir);
  });
});

describe("isFilledRemoteDir", () => {
  it.each([
    ["odo", ODO_GEN150, true],
    ["odo", `${ODO_GEN150}/`, true],
    ["odo", "/gpfs/wolf2/olcf/gen150/proj-shared/sam/v2", false],
    ["odo", "/lustre/orion/gen150/proj-shared/vista", false],
    ["lux", "/lustre/orion/stf218/proj-shared/vista", true],
    ["perlmutter", ODO_GEN150, false],
  ] as const)("%s %j → %j", (cluster, value, filled) => {
    expect(isFilledRemoteDir(cluster, value)).toBe(filled);
  });
});

describe("remoteDirFill", () => {
  it.each([
    ["an empty field fills", "odo", "gen150-vista", "", ODO_GEN150],
    ["a blank field fills", "odo", "gen150-vista", "  ", ODO_GEN150],
    ["VISTA's folder for another project refills", "odo", "stf218", ODO_GEN150, ODO_STF218],
    ["VISTA's folder for the same project is left", "odo", "gen150-vista", ODO_GEN150, null],
    ["a trailing slash still counts as filled", "odo", "stf218", `${ODO_GEN150}/`, ODO_STF218],
    ["a trailing slash on the same folder is left", "odo", "gen150", `${ODO_GEN150}/`, null],
    ["a typed path is left", "odo", "stf218", "/gpfs/wolf2/olcf/gen150/proj-shared/sam/v2", null],
    ["Lux uses the Frontier template", "lux", "stf218", "", "/lustre/orion/stf218/proj-shared/vista"],
    ["no project, nothing to fill", "odo", null, "", null],
    ["Perlmutter is never filled", "perlmutter", "m1234", "", null],
  ] as const)("%s", (_name, cluster, project, current, expected) => {
    expect(remoteDirFill(cluster, project, current)).toBe(expected);
  });
});

describe("filledNote", () => {
  it("names the claim and the folder when they differ", () => {
    expect(filledNote("odo", "gen150-vista", ODO_GEN150)).toBe(
      "Filled from your S3M token's project (gen150 folder inferred from gen150-vista).",
    );
  });

  it("names the project once when it is the folder", () => {
    expect(filledNote("frontier", "chm243", "/lustre/orion/chm243/proj-shared/vista")).toBe(
      "Filled from your S3M token's project, chm243.",
    );
    expect(filledNote("lux", "stf218", "/lustre/orion/stf218/proj-shared/vista")).toBe(
      "Filled from your S3M token's project, stf218.",
    );
  });

  it.each([
    ["an edited path", "odo", "gen150-vista", "/gpfs/wolf2/olcf/gen150/proj-shared/sam"],
    ["another project's folder", "odo", "stf218", ODO_GEN150],
    ["an empty field", "odo", "gen150-vista", ""],
    ["no project", "odo", null, ODO_GEN150],
  ] as const)("no note for %s", (_name, cluster, project, current) => {
    expect(filledNote(cluster, project, current)).toBeNull();
  });
});
