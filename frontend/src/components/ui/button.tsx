import { Slot } from "radix-ui";
import { forwardRef, type ButtonHTMLAttributes } from "react";
import { cn } from "@/lib/utils";

const variants = {
  primary:
    "bg-accent text-accent-fg hover:bg-accent-hover shadow-[inset_0_1px_0_rgba(255,255,255,0.18),0_1px_2px_rgba(11,11,11,0.12),0_4px_12px_-4px_var(--ring)]",
  secondary: "bg-surface-2 text-fg hover:bg-surface-3",
  outline: "border border-line-strong bg-surface text-fg hover:bg-surface-2 hover:border-[var(--text-3)]/40 shadow-sm",
  ghost: "text-fg-2 hover:bg-surface-2 hover:text-fg",
  danger: "bg-danger text-white hover:opacity-90",
  ultra:
    "sheen bg-gradient-to-r from-ultra to-ultra-2 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.25),0_6px_18px_-8px_var(--ultra)] hover:brightness-105",
  inverted: "bg-fg text-bg hover:opacity-90",
} as const;

const sizes = {
  xs: "h-7 px-2.5 text-xs gap-1.5 rounded-md",
  sm: "h-8 px-3 text-[13px] gap-1.5 rounded-lg",
  md: "h-10 px-4 text-sm gap-2 rounded-lg",
  lg: "h-12 px-5 text-[15px] gap-2 rounded-xl",
  icon: "h-9 w-9 rounded-lg",
  "icon-sm": "h-8 w-8 rounded-lg",
} as const;

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  asChild?: boolean;
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { className, variant = "primary", size = "md", asChild, loading, disabled, children, ...props },
  ref,
) {
  const Comp = asChild ? Slot.Root : "button";
  return (
    <Comp
      ref={ref}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center font-medium whitespace-nowrap transition-[background-color,color,border-color,box-shadow,transform,opacity,filter] duration-150 ease-[var(--ease-out)] active:scale-[0.97] disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {asChild ? (
        children
      ) : (
        <>
          {loading && <span className="size-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden />}
          {children}
        </>
      )}
    </Comp>
  );
});
