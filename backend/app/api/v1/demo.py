"""Demo-mode product images (SVG illustrations) for the Mock Marketplace Provider.

Real listings link to the marketplace's own photos; the simulated market needs images that are
license-free, so it uses these generated garment illustrations.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Query
from fastapi.responses import Response

router = APIRouter(prefix="/demo", tags=["demo"], include_in_schema=False)

COLORS = {
    "nero": "#25272d",
    "nera": "#25272d",
    "bianco": "#f3f2ee",
    "bianca": "#f3f2ee",
    "blu": "#233b7a",
    "azzurro": "#86c5ec",
    "celeste": "#9fd3f2",
    "rosso": "#c8313a",
    "rossa": "#c8313a",
    "bordeaux": "#6e1f2b",
    "verde": "#2f7a4a",
    "khaki": "#6b6b3f",
    "grigio": "#9aa0a8",
    "grigia": "#9aa0a8",
    "beige": "#d8c7a2",
    "marrone": "#7a4b2d",
    "giallo": "#f2c230",
    "gialla": "#f2c230",
    "arancione": "#ee7a2c",
    "rosa": "#f0a6c4",
    "viola": "#6f4bb8",
    "multicolore": "#4f7cd8",
    "fantasia": "#c85f8a",
}
BACKGROUNDS = ("#ece9e4", "#e7ebef", "#efe8e1", "#e5e9e3", "#ebe6ee")

TOP = "M70 70 L118 52 Q150 72 182 52 L230 70 L268 128 L228 150 L214 128 L214 262 L86 262 L86 128 L72 150 L32 128 Z"
POLO_COLLAR = "M118 52 L150 92 L182 52 L170 50 L150 74 L130 50 Z"
HOOD = "M112 56 Q150 4 188 56 Q150 86 112 56 Z"
JACKET = "M66 66 L120 48 L150 70 L180 48 L234 66 L270 200 L232 212 L220 140 L220 270 L80 270 L80 140 L68 212 L30 200 Z"
PUFFER = "M62 70 L118 46 Q150 62 182 46 L238 70 L272 206 L232 216 L222 150 L222 272 L78 272 L78 150 L68 216 L28 206 Z"
COAT = "M80 56 L126 44 L150 64 L174 44 L220 56 L254 212 L222 220 L214 150 L224 288 L76 288 L86 150 L78 220 L46 212 Z"
PANTS = "M96 44 L204 44 L216 282 L166 282 L150 120 L134 282 L84 282 Z"
SNEAKER = "M40 200 Q46 150 96 146 L150 130 Q178 124 196 150 L240 170 Q268 180 266 206 L264 222 L40 222 Z"
SOLE = "M36 214 L268 214 Q270 236 250 238 L52 238 Q34 236 36 214 Z"
BELT = "M30 140 L240 140 L240 172 L30 172 Z M226 128 L276 128 L276 184 L226 184 Z"
SCARF = "M90 40 L150 40 L150 250 L176 290 L120 290 L120 120 L90 120 Z M150 40 L210 40 L210 120 L180 120 Z"
CAP = "M70 170 Q72 92 150 88 Q228 92 230 170 Z M150 170 L262 176 Q262 196 230 196 L150 186 Z"


def _shade(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, round(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def garment_svg(category: str, color: str, variant: int) -> str:
    fill = COLORS.get(color, "#8892a0")
    dark = _shade(fill, 0.78)
    light = _shade(fill, 1.12)
    bg = BACKGROUNDS[variant % len(BACKGROUNDS)]
    shapes: list[str]
    if category in ("sneakers",):
        shapes = [
            f'<path d="{SNEAKER}" fill="{fill}"/>',
            f'<path d="{SOLE}" fill="#f6f6f4" stroke="{dark}" stroke-width="2"/>',
            f'<path d="M110 160 L176 150 M118 176 L186 166" stroke="{light}" stroke-width="6" stroke-linecap="round"/>',
        ]
    elif category in ("jeans", "trousers", "tracksuits"):
        shapes = [
            f'<path d="{PANTS}" fill="{fill}"/>',
            f'<path d="M96 44 L204 44 L205 64 L95 64 Z" fill="{dark}"/>',
            f'<path d="M150 64 L150 116" stroke="{dark}" stroke-width="3"/>',
        ]
    elif category in ("puffer-jackets",):
        shapes = (
            [f'<path d="{PUFFER}" fill="{fill}"/>']
            + [
                f'<path d="M80 {y} L220 {y}" stroke="{dark}" stroke-width="3" opacity="0.6"/>'
                for y in (110, 150, 190, 230)
            ]
            + [f'<path d="M150 66 L150 272" stroke="{dark}" stroke-width="4"/>']
        )
    elif category in ("coats",):
        shapes = [
            f'<path d="{COAT}" fill="{fill}"/>',
            f'<path d="M150 64 L150 288" stroke="{dark}" stroke-width="3"/>',
            f'<circle cx="138" cy="130" r="5" fill="{dark}"/><circle cx="138" cy="170" r="5" fill="{dark}"/><circle cx="138" cy="210" r="5" fill="{dark}"/>',
        ]
    elif category in ("jackets", "fleece"):
        shapes = [
            f'<path d="{JACKET}" fill="{fill}"/>',
            f'<path d="M150 70 L150 270" stroke="{dark}" stroke-width="3"/>',
            f'<path d="M100 190 L130 190 M170 190 L200 190" stroke="{dark}" stroke-width="4"/>',
        ]
    elif category in ("belts",):
        shapes = [
            f'<path d="{BELT}" fill="{fill}" fill-rule="evenodd"/>',
            '<rect x="232" y="136" width="38" height="40" rx="4" fill="#c9a24a"/>',
        ]
    elif category in ("scarves",):
        shapes = [
            f'<path d="{SCARF}" fill="{fill}"/>',
            f'<path d="M120 200 L150 200 M120 230 L150 230" stroke="{light}" stroke-width="6"/>',
        ]
    elif category in ("caps", "bags"):
        shapes = [f'<path d="{CAP}" fill="{fill}"/>']
    else:
        shapes = [
            f'<path d="{TOP}" fill="{fill}"/>',
            f'<path d="M86 246 L214 246" stroke="{dark}" stroke-width="5"/>',
        ]
        if category == "hoodies":
            shapes.append(f'<path d="{HOOD}" fill="{dark}"/>')
            shapes.append(
                f'<rect x="112" y="186" width="76" height="40" rx="8" fill="{dark}" opacity="0.5"/>'
            )
        elif category == "polo-shirts":
            shapes.append(f'<path d="{POLO_COLLAR}" fill="{light}"/>')
        elif category == "shirts":
            shapes.append(f'<path d="M150 70 L150 262" stroke="{dark}" stroke-width="2"/>')
            shapes.append(f'<path d="{POLO_COLLAR}" fill="{light}"/>')
        elif category == "football-shirts":
            shapes.append(f'<path d="M126 110 L174 110 L174 230 L126 230 Z" fill="{light}" opacity="0.55"/>')
        elif category == "knitwear":
            shapes += [
                f'<path d="M{x} 100 L{x} 240" stroke="{dark}" stroke-width="2" opacity="0.45"/>'
                for x in range(100, 210, 14)
            ]
    rotate = (variant % 3 - 1) * 4
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 400" width="600" height="800">'
        f'<rect width="300" height="400" fill="{bg}"/>'
        f'<ellipse cx="150" cy="340" rx="110" ry="14" fill="#000" opacity="0.06"/>'
        f'<g transform="translate(0 30) rotate({rotate} 150 170)">{"".join(shapes)}</g>'
        "</svg>"
    )


@router.get("/images/{category}.svg")
async def demo_image(
    category: str,
    color: str = Query("grigio", max_length=30),
    v: int = Query(0, ge=0, le=1_000_000),
    i: int = Query(0, ge=0, le=50),
) -> Response:
    safe_category = "".join(c for c in category if c.isalnum() or c == "-")[:40]
    svg = garment_svg(safe_category, color.lower(), v + i)
    etag = hashlib.md5(svg.encode(), usedforsecurity=False).hexdigest()
    return Response(
        svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800, immutable", "ETag": etag},
    )
