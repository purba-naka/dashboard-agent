"use client";

import type { ComponentProps } from "react";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

type IconActionProps = Omit<ComponentProps<typeof Button>, "aria-label" | "variant" | "size"> & {
  /** Nama aksesibel tombol. */
  label: string;
  /** Teks tooltip bila beda dari label (mis. label memuat nama entri yang panjang). */
  tip?: string;
};

/** Tombol ikon ghost dengan Tooltip. Provider lokal agar panel bisa dipakai mandiri. */
export function IconAction({ label, tip, ...props }: IconActionProps) {
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button type="button" variant="ghost" size="icon-sm" aria-label={label} {...props} />
        </TooltipTrigger>
        <TooltipContent>{tip ?? label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
