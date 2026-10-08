"use client";

import { Bell, Import, LogOut, Monitor, Moon, ScanSearch, Search, Settings, Sun } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
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
  if (data && !data.provider.configured) {
    // No automatic source: nothing scans, everything comes from what you capture.
    return (
      <Tip content={`Source: your captures (extension, links, email) · ${data.listings_tracked.toLocaleString()} listings tracked`}>
        <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-2.5 py-1 text-xs whitespace-nowrap text-fg-2">
          <span className="relative size-2 rounded-full bg-accent" aria-hidden />
          {compact ? "Captures" : `Your captures · ${data.listings_tracked.toLocaleString()}`}
        </span>
      </Tip>
    );
  }
  return (
    <Tip content={data ? `Source: ${data.provider.name} + your captures · ${data.listings_tracked.toLocaleString()} listings tracked` : "Connecting…"}>
      <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface px-2.5 py-1 text-xs whitespace-nowrap text-fg-2">
        <span className="relative flex size-2" aria-hidden>
          {!stale && <span className="absolute inset-0 rounded-full bg-success opacity-60 [animation:ping_1.8s_var(--ease-out)_infinite]" />}
          <span className={cn("relative size-2 rounded-full", stale ? "bg-warning" : "bg-success")} />
        </span>
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
        <MenuItem onSelect={() => router.push("/import")}>
          <Import /> Import a Vinted search
        </MenuItem>
        {NAV.filter((n) => !n.mobile).map((n) => (
          <MenuItem key={n.href} onSelect={() => router.push(n.href)} className="lg:hidden">
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
  // Ring the bell when new alerts arrive (not on first load): adjust state during render.
  const [prev, setPrev] = useState<number | null>(null);
  const [rings, setRings] = useState(0);
  if (data && n !== prev) {
    setPrev(n);
    if (prev !== null && n > prev) setRings(rings + 1);
  }
  return (
    <Link
      href="/alerts"
      className="press relative flex size-9 items-center justify-center rounded-lg text-fg-2 transition-[background-color,color,transform] duration-150 hover:bg-surface-2 hover:text-fg"
      aria-label={n ? `${n} unread alerts` : "Alerts"}
    >
      <Bell key={rings} className={cn("size-[18px] origin-top", rings > 0 && "animate-wiggle")} />
      {n > 0 && (
        <span
          key={n > 99 ? "99+" : n}
          className="animate-scale-in absolute right-0.5 top-0.5 flex min-w-4 items-center justify-center rounded-full bg-ultra-solid px-1 text-[10px] font-bold leading-4 text-white ring-2 ring-bg"
        >
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
    <form onSubmit={submit} className={cn("relative", className)} role="search" aria-label="Quick search">
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-fg-3" />
      <input
        value={q}
        onChange={(e) => setQ(e.target.value)}
        autoFocus={autoFocus}
        placeholder='Try "felpe Ralph Lauren sotto 25€ con ROI 50%"'
        aria-label="Search deals in natural language"
        className="h-10 w-full rounded-xl border border-line bg-surface-2 pl-9 pr-3 text-sm text-fg transition-[background-color,border-color,box-shadow] duration-200 placeholder:text-fg-3 focus:border-accent focus:bg-surface focus:outline-none focus:ring-4 focus:ring-[var(--ring)]"
      />
    </form>
  );
}

const DESKTOP_NAV = NAV.filter((n) => n.href !== "/alerts" && n.href !== "/settings");

/** Main navigation with a pill that slides to the active page (positioned via refs, no re-render). */
function DesktopNav({ pathname }: { pathname: string }) {
  const navRef = useRef<HTMLElement>(null);
  const pillRef = useRef<HTMLSpanElement>(null);
  const active = DESKTOP_NAV.find((n) => isActive(pathname, n.href))?.href ?? null;

  useLayoutEffect(() => {
    const nav = navRef.current;
    const pill = pillRef.current;
    if (!nav || !pill) return;
    const place = () => {
      const el = active ? nav.querySelector<HTMLElement>(`[data-href="${active}"]`) : null;
      if (!el) {
        pill.style.opacity = "0";
        return;
      }
      pill.style.width = `${el.offsetWidth}px`;
      pill.style.transform = `translateX(${el.offsetLeft}px)`;
      pill.style.opacity = "1";
    };
    place();
    // Enable the slide only after the first placement, so it never flies in on page load.
    const raf = requestAnimationFrame(() => (pill.dataset.ready = "true"));
    const ro = new ResizeObserver(place);
    ro.observe(nav);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [active]);

  return (
    <nav ref={navRef} className="relative ml-4 hidden items-center gap-0.5 lg:flex" aria-label="Main">
      <span
        ref={pillRef}
        aria-hidden
        className="absolute left-0 top-0 h-full rounded-lg bg-surface-2 opacity-0 shadow-[inset_0_0_0_1px_var(--border)] data-[ready=true]:transition-[transform,width,opacity] data-[ready=true]:duration-300 data-[ready=true]:ease-[var(--ease-out)]"
      />
      {DESKTOP_NAV.map((n) => (
        <Link
          key={n.href}
          href={n.href}
          data-href={n.href}
          aria-current={active === n.href ? "page" : undefined}
          className={cn(
            "relative rounded-lg px-3 py-1.5 text-[13px] font-medium whitespace-nowrap transition-colors duration-200",
            active === n.href ? "text-fg" : "text-fg-2 hover:text-fg",
          )}
        >
          {n.label}
        </Link>
      ))}
    </nav>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const headerRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const header = headerRef.current;
    if (!header) return;
    const onScroll = () => (header.dataset.scrolled = String(window.scrollY > 4));
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const mobileNav = [...NAV.filter((n) => n.mobile), NAV.find((n) => n.href === "/analytics")!];

  return (
    <div className="min-h-dvh">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-3 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-fg focus:shadow-pop"
      >
        Skip to content
      </a>
      <AlertsWatcher />
      <header
        ref={headerRef}
        className="sticky top-0 z-40 border-b border-transparent bg-bg/75 backdrop-blur-xl backdrop-saturate-150 transition-[border-color,box-shadow] duration-300 data-[scrolled=true]:border-line data-[scrolled=true]:shadow-[0_8px_24px_-20px_rgba(0,0,0,0.35)]"
      >
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4 sm:px-6">
          <Link href="/" className="press shrink-0" aria-label="FlipFinder home">
            <Logo />
          </Link>
          <DesktopNav pathname={pathname} />
          <GlobalSearch className="ml-auto hidden w-full max-w-sm md:block lg:max-w-[260px] xl:max-w-sm" />
          <div className="ml-auto flex items-center gap-1.5 md:ml-2">
            <div className="hidden 2xl:block">
              <ScanStatus />
            </div>
            <Link
              href="/search"
              className="press flex size-9 items-center justify-center rounded-lg text-fg-2 transition-colors hover:bg-surface-2 md:hidden"
              aria-label="Search"
            >
              <Search className="size-[18px]" />
            </Link>
            <AlertBell />
            <Link
              href="/settings"
              className="press hidden size-9 items-center justify-center rounded-lg text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg lg:flex"
              aria-label="Settings"
            >
              <Settings className="size-[18px]" />
            </Link>
            <UserMenu />
          </div>
        </div>
      </header>
      <main id="main" tabIndex={-1} className="mx-auto max-w-[1400px] px-4 pb-28 pt-5 outline-none sm:px-6 lg:pb-12">
        {/* Re-keyed per route: a short fade-up on every navigation. */}
        <div key={pathname} className="animate-fade-up">
          {children}
        </div>
      </main>
      <nav
        className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-bg/85 backdrop-blur-xl backdrop-saturate-150 safe-bottom lg:hidden"
        aria-label="Main mobile"
      >
        <div className="mx-auto grid max-w-md grid-cols-5">
          {mobileNav.map((n) => {
            const active = isActive(pathname, n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "press relative flex flex-col items-center gap-0.5 py-2.5 text-[10px] font-medium transition-colors duration-200",
                  active ? "text-accent" : "text-fg-2",
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "absolute top-0 h-0.5 w-8 rounded-full bg-accent transition-[transform,opacity] duration-300 ease-[var(--ease-out)]",
                    active ? "scale-x-100 opacity-100" : "scale-x-0 opacity-0",
                  )}
                />
                <n.icon className={cn("size-5 transition-transform duration-200 ease-[var(--ease-out)]", active && "-translate-y-px")} strokeWidth={active ? 2.2 : 1.8} />
                {n.label}
              </Link>
            );
          })}
        </div>
      </nav>
    </div>
  );
}
