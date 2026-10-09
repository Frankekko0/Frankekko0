import { Archive, Bell, Bookmark, Briefcase, LayoutDashboard, LineChart, Radar, Settings, ShieldCheck, Tag, Target, Wallet } from "lucide-react";

export const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard, mobile: true },
  { href: "/deals", label: "Deals", icon: Radar, mobile: true },
  { href: "/watchlists", label: "Watchlists", icon: Target, mobile: true },
  { href: "/flips", label: "My Flips", icon: Wallet, mobile: true },
  { href: "/selling", label: "Selling", icon: Tag, mobile: false },
  { href: "/business", label: "Business", icon: Briefcase, mobile: false },
  { href: "/autonomy", label: "Autonomy", icon: ShieldCheck, mobile: false },
  { href: "/analytics", label: "Analytics", icon: LineChart, mobile: false },
  { href: "/saved", label: "Saved", icon: Bookmark, mobile: false },
  { href: "/items", label: "Archive", icon: Archive, mobile: false },
  { href: "/alerts", label: "Alerts", icon: Bell, mobile: false },
  { href: "/settings", label: "Settings", icon: Settings, mobile: false },
] as const;

export function isActive(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}
