"use client";

// Dark Genesis-branded top bar that sits above the main content (right of the
// NavRail). Shows the Genesis AmSC lockup + VISTA wordmark on the left and the
// active project chip on the right when one is selected.

import Image from "next/image";
import { useActiveProject } from "@/lib/projects";

export default function TopBar() {
  const { activeProject } = useActiveProject();

  return (
    <header className="h-12 flex items-center px-3 bg-[#112a4d] border border-[#004573] text-white">
      <div className="flex items-center gap-3 mr-auto min-w-0">
        <Image
          src="/genesis-amsc-lockup-horizontal-white-cropped.svg"
          alt="Genesis AmSC"
          width={520}
          height={30}
          className="block h-[30px] w-auto max-w-[min(70vw,520px)] object-contain object-left flex-shrink-0"
        />
        <div className="font-bold text-[18px] tracking-[0.08em] text-[#f2f2f2] whitespace-nowrap leading-[1.1]">
          VISTA
        </div>
      </div>
      {activeProject && (
        <div
          className="inline-flex items-center gap-2 rounded-full bg-white/[0.08] pl-3 pr-3 py-1 text-[#e6ebf2] text-xs"
          title="Active project"
        >
          <span className="text-[10px] uppercase tracking-[0.6px] opacity-70">Project</span>
          <span className="font-semibold">{activeProject.name}</span>
        </div>
      )}
    </header>
  );
}
