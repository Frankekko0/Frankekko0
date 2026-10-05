"use client";

import { Dialog as RDialog, DropdownMenu as RMenu, Slider as RSlider, Switch as RSwitch, Tabs as RTabs, Tooltip as RTooltip } from "radix-ui";
import { X } from "lucide-react";
import { useState, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/* ---------------------------------------------------------------- Switch */
export function Switch({ className, ...props }: ComponentProps<typeof RSwitch.Root>) {
  return (
    <RSwitch.Root
      className={cn(
        "relative inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full bg-surface-3 transition-colors duration-200 data-[state=checked]:bg-accent disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <RSwitch.Thumb className="block size-5 translate-x-0.5 rounded-full bg-white shadow-[0_1px_3px_rgba(0,0,0,0.25)] transition-[translate,width] duration-200 ease-[var(--ease-out)] data-[state=checked]:translate-x-[18px]" />
    </RSwitch.Root>
  );
}

/* ---------------------------------------------------------------- Dialog */
export const Dialog = RDialog.Root;
export const DialogTrigger = RDialog.Trigger;
export const DialogClose = RDialog.Close;

export function DialogContent({
  title,
  description,
  children,
  className,
  side = "center",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
  side?: "center" | "right";
}) {
  return (
    <RDialog.Portal>
      <RDialog.Overlay className="overlay-anim fixed inset-0 z-50 bg-black/45 backdrop-blur-[3px]" />
      <RDialog.Content
        className={cn(
          "fixed z-50 flex flex-col bg-surface shadow-pop outline-none",
          side === "center"
            ? "dialog-anim inset-x-0 bottom-0 max-h-[92dvh] rounded-t-3xl border-t border-line sm:inset-auto sm:left-1/2 sm:top-1/2 sm:w-full sm:max-w-lg sm:-translate-x-1/2 sm:-translate-y-1/2 sm:rounded-2xl sm:border"
            : "drawer-anim inset-y-0 right-0 w-full max-w-md border-l border-line",
          className,
        )}
      >
        {side === "center" && <div className="mx-auto mt-2.5 h-1 w-10 shrink-0 rounded-full bg-line-strong sm:hidden" aria-hidden />}
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div>
            <RDialog.Title className="text-base font-semibold tracking-tight">{title}</RDialog.Title>
            {description ? (
              <RDialog.Description className="mt-0.5 text-[13px] text-fg-3">{description}</RDialog.Description>
            ) : (
              <RDialog.Description className="sr-only">{title}</RDialog.Description>
            )}
          </div>
          <RDialog.Close className="press -mr-1 rounded-lg p-1.5 text-fg-3 transition-colors hover:bg-surface-2 hover:text-fg" aria-label="Close">
            <X className="size-4" />
          </RDialog.Close>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 safe-bottom">{children}</div>
      </RDialog.Content>
    </RDialog.Portal>
  );
}

/* ------------------------------------------------------------------ Tabs */
export const Tabs = RTabs.Root;
export const TabsContent = RTabs.Content;

export function TabsList({ className, ...props }: ComponentProps<typeof RTabs.List>) {
  return (
    <RTabs.List
      className={cn("scrollbar-none inline-flex max-w-full items-center gap-1 overflow-x-auto rounded-xl bg-surface-2 p-1", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: ComponentProps<typeof RTabs.Trigger>) {
  return (
    <RTabs.Trigger
      className={cn(
        "rounded-lg px-3 py-1.5 text-[13px] font-medium whitespace-nowrap text-fg-2 transition-[background-color,color,box-shadow] duration-200 hover:text-fg data-[state=active]:bg-surface data-[state=active]:text-fg data-[state=active]:shadow-card",
        className,
      )}
      {...props}
    />
  );
}

/* --------------------------------------------------------------- Tooltip */
export function Tip({ content, children, side = "top" }: { content: ReactNode; children: ReactNode; side?: "top" | "bottom" | "left" | "right" }) {
  return (
    <RTooltip.Root delayDuration={150}>
      <RTooltip.Trigger asChild>{children}</RTooltip.Trigger>
      <RTooltip.Portal>
        <RTooltip.Content
          side={side}
          sideOffset={6}
          className="popover-anim z-50 max-w-64 origin-[var(--radix-tooltip-content-transform-origin)] rounded-lg bg-fg px-2.5 py-1.5 text-xs leading-relaxed text-bg shadow-pop"
        >
          {content}
        </RTooltip.Content>
      </RTooltip.Portal>
    </RTooltip.Root>
  );
}

export const TooltipProvider = RTooltip.Provider;

/* ----------------------------------------------------------------- Menu */
export const Menu = RMenu.Root;
export const MenuTrigger = RMenu.Trigger;

export function MenuContent({ className, ...props }: ComponentProps<typeof RMenu.Content>) {
  return (
    <RMenu.Portal>
      <RMenu.Content
        sideOffset={8}
        align="end"
        className={cn(
          "popover-anim z-50 min-w-52 origin-[var(--radix-dropdown-menu-content-transform-origin)] rounded-xl border border-line bg-surface/95 p-1 shadow-pop backdrop-blur-xl",
          className,
        )}
        {...props}
      />
    </RMenu.Portal>
  );
}

export function MenuItem({ className, ...props }: ComponentProps<typeof RMenu.Item>) {
  return (
    <RMenu.Item
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] text-fg-2 outline-none transition-colors duration-100 data-[highlighted]:bg-surface-2 data-[highlighted]:text-fg [&_svg]:size-4",
        className,
      )}
      {...props}
    />
  );
}

export function MenuSeparator() {
  return <RMenu.Separator className="my-1 h-px bg-line" />;
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <RMenu.Label className="px-2.5 py-1.5 text-xs text-fg-3">{children}</RMenu.Label>;
}

/* ---------------------------------------------------------------- Slider */

export function RangeSlider({
  value,
  onCommit,
  min = 0,
  max = 100,
  step = 1,
  label,
  format = (v: number) => String(v),
}: {
  value: number;
  onCommit: (v: number) => void;
  min?: number;
  max?: number;
  step?: number;
  label: string;
  format?: (v: number) => string;
}) {
  const [local, setLocal] = useState(value);
  const [prev, setPrev] = useState(value);
  if (prev !== value) {
    // Adopt external changes (e.g. filters reset) without an effect.
    setPrev(value);
    setLocal(value);
  }
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-[13px]">
        <span className="font-medium text-fg-2">{label}</span>
        <span className="font-semibold text-fg tnum">{format(local)}</span>
      </div>
      <RSlider.Root
        className="relative flex h-5 w-full touch-none select-none items-center"
        value={[local]}
        min={min}
        max={max}
        step={step}
        onValueChange={([v]) => setLocal(v ?? min)}
        onValueCommit={([v]) => onCommit(v ?? min)}
        aria-label={label}
      >
        <RSlider.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-surface-3">
          <RSlider.Range className="absolute h-full rounded-full bg-accent" />
        </RSlider.Track>
        <RSlider.Thumb className="block size-[18px] rounded-full border-2 border-accent bg-surface shadow-card outline-none transition-transform duration-150 ease-[var(--ease-out)] hover:scale-110 active:scale-115 focus-visible:ring-4 focus-visible:ring-[var(--ring)]" />
      </RSlider.Root>
    </div>
  );
}

/* ------------------------------------------------------------------ Chip */
export function Chip({ active, children, onClick, className }: { active?: boolean; children: ReactNode; onClick?: () => void; className?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "press inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[13px] font-medium whitespace-nowrap transition-[background-color,border-color,color,transform] duration-150 [&_svg]:size-3.5",
        active ? "border-accent bg-accent-soft text-accent" : "border-line bg-surface text-fg-2 hover:border-line-strong hover:text-fg",
        className,
      )}
    >
      {children}
    </button>
  );
}
