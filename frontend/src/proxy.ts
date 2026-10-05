import { NextResponse, type NextRequest } from "next/server";

const PUBLIC = ["/login", "/register", "/offline"];

/** Redirects visitors without a session cookie to the login page (the API still validates it). */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = request.cookies.has("ff_session");
  const isPublic = PUBLIC.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  if (!hasSession && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|docs|_next/static|_next/image|icons|icon.png|manifest.webmanifest|sw.js|favicon.ico|robots.txt).*)"],
};
