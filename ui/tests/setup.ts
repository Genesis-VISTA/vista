import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Testing Library's auto-cleanup only registers itself when a global `afterEach`
// exists, and this suite does not enable Vitest globals.
afterEach(cleanup);
