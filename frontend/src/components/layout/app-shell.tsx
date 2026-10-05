"use client";

import { Bell, LogOut, Menu as MenuIcon, Monitor, Moon, ScanSearch, Search, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api } from "@/lib/api";
import { timeAgo } from "@/lib/format";
import { useMe, useSystemStatus, useUnreadCount } from "@/lib/queries";
import { cn } from "@/lib/utils";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Tip } from "@/components/ui/misc";
import { Logo } from "./brand";
import { AlertsWatcher } from "./alerts-watcher";
import { NAV, isActive } from "./nav-items";

function useNow(intervalMs = 15_000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}

export function ScanStatus({ compact = false }: { compact?: boolean }) {
  const { data } = useSystemStatus();
  const now = useNow();
  const last = (data?.scanner.last_run?.at as string | undefined) ?? null;
  const stale = last ? now - new Date(last).getTime() > 5 * 60_000 : true;
  return (
    <Tip content={data ? `${data.provider.demo_mode ? "Demo market data" : `Source: ${data.provider.name}`} · ${data.listings_tracked.toLocaleString()} listings tracked` : "Connecting…"}>
      <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-fg-2">
        <span className={cn("size-2 rounded-full", stale ? "bg-warning" : "animate-pulse-dot bg-success")} aria-hidden />
        {compact ? (stale ? "Idle" : "Live") : stale ? "Scanner idle" : `Live · scanned ${timeAgo(last, now)}`}
      </span>
    </Tip>
  );
}

function ThemeMenuItems() {
  const { setTheme } = useTheme();
  return (
    <>
      <MenuLabel>Theme</MenuLabel>
      <MenuItem onSelect={() => setTheme("light")}>
        <Sun /> Light
      </MenuItem>
      <MenuItem onSelect={() => setTheme("dark")}>
        <Moon /> Dark
      </MenuItem>
      <MenuItem onSelect={() => setTheme("system")}>
        <Monitor /> System
      </MenuItem>
    </>
  );
}

function UserMenu() {
  const { data: me } = useMe();
  const router = useRouter();
  const initials = (me?.display_name ?? me?.email ?? "?").slice(0, 1).toUpperCase();
  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    router.replace("/login");
  }
  return (
    <Menu>
      <MenuTrigger
        className="flex size-9 items-center justify-center rounded-full bg-surface-3 text-[13px] font-semibold text-fg outline-none hover:ring-4 hover:ring-[var(--ring)]"
        aria-label="Account menu"
      >
        {initials}
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>{me?.email}</MenuLabel>
        <MenuSeparator />
        <MenuItem onSelect={() => router.push("/analyze")}>
          <ScanSearch /> Analyze a listing
        </MenuItem>
        {NAV.filter((n) => !n.mobile).map((n) => (
          <MenuItem key={n.href} onSelect={() => router.push(n.href)} className="md:hidden">
            <n.icon /> {n.label}
          </MenuItem>
        ))}
        <ThemeMenuItems />
        <MenuSeparator />
        <MenuItem onSelect={logout}>
          <LogOut /> Log out
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}

function AlertBell() {
  const { data } = useUnreadCount();
  const n = data?.unread ?? 0;
  return (
    <Link
      href="/alerts"
      className="relative flex size-9 items-center justify-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg"
      aria-label={n ? `${n} unread alerts` : "Alerts"}
    >
      <Bell className="size-[18px]" />
      {n > 0 && (
        <span className="absolute right-1 top-1 flex min-w-4 items-center justify-center rounded-full bg-ultra px-1 text-[10px] font-bold leading-4 text-white">
          {n > 99 ? "99+" : n}
        </span>
      )}
    </Link>
  );
}

export function GlobalSearch({ className, autoFocus }: { className?: string; autoFocus?: boolean }) {
  const router = useRouter();
  const [q, setQ] = useState("");
  function submit(e: FormEvent) {
    e.preventDefault();
    if (q.trim()) router.push(`/search?q=${encodeURIComponent(q.trim())}`);
  }
  return (
    <form onSubmit={submit} className={cn("relative", className)} role="search">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-3" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus={autoFocus}
        placeholder='Try "felpe Ralph Lauren sotto 25€ con ROI 50%"'
        aria-label="Search deals in natural language"
        className="h-10 w-full rounded-xl border border-line bg-surface-2 pl-9 pr-3 text-sm text-fg placeholder:text-fg-3 focus:border-accent focus:bg-surface focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
      />
    </form>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="min-h-dvh">
      <AlertsWatcher />
      <header className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-xl">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
          <Link href="/" className="shrink-0" aria-label="FlipFinder home">
            <Logo />
          </Link>
          <nav className="ml-4 hidden items-center gap-0.5 lg:flex" aria-label="Main">
            {NAV.filter((n) => n.href !== "/alerts" && n.href !== "/settings").map((n) => (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "rounded-lg px-3 py-1.5 text-[13px] font-medium transition-colors",
                  isActive(pathname, n.href) ? "bg-surface-2 text-fg" : "text-fg-2 hover:text-fg",
                )}
              >
                {n.label}
              </Link>
            ))}
          </nav>
          <GlobalSearch className="ml-auto hidden w-full max-w-sm md:block" />
          <div className="ml-auto flex items-center gap-1.5 md:ml-2">
            <div className="hidden xl:block">
              <ScanStatus />
            </div>
            <Link href="/search" className="flex size-9 items-center justify-center rounded-lg text-fg-2 hover:bg-surface-2 md:hidden" aria-label="Search">
              <Search className="size-[18px]" />
            </Link>
            <AlertBell />
            <Link
              href="/settings"
              className="hidden size-9 items-center justify-center rounded-lg text-fg-2 hover:bg-surface-2 hover:text-fg lg:flex"
              aria-label="Settings"
            >
              <MenuIcon className="size-[18px]" />
            </Link>
            <UserMenu />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 pb-28 pt-5 sm:px-6 lg:pb-12">{children}</main>
      <nav
        className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/90 backdrop-blur-xl safe-bottom lg:hidden"
        aria-label="Main mobile"
      >
        <div className="mx-auto grid max-w-md grid-cols-5">
          {[...NAV.filter((n) => n.mobile), NAV.find((n) => n.href === "/analytics")!].map((n) => {
            const active = isActive(pathname, n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={cn("flex flex-col items-center gap-0.5 py-2.5 text-[10px] font-medium", active ? "text-accent" : "text-fg-3")}
              >
                <n.icon className="size-5" strokeWidth={active ? 2.2 : 1.8} />
                {n.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
