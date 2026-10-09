"""Local OCR: label text without any model key (size, composition, RN/CA, country, brand)."""

from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFont

from app.analysis.categories import CLOTHING
from app.analysis.dossier import PARTIAL, build_dossier
from app.analysis.labels import build_label_report
from app.vision import ocr as ocr_mod
from app.vision.analyzer import HeuristicImageAnalyzer
from app.vision.ocr import OcrLine, parse_ocr, set_ocr_engine
from tests.photos import jpeg, photo
from tests.unit.test_dossier import facts


def line(text: str, conf: float = 0.85) -> OcrLine:
    return OcrLine(text, conf, [0.1, 0.1, 0.5, 0.1])


@pytest.fixture(autouse=True)
def reset_engine() -> Any:
    yield
    set_ocr_engine(None)
    ocr_mod._tried = False  # forget the override: the next test asks again


# ------------------------------------------------------------------ parsing what OCR reads
def test_a_clothing_label_is_read_into_facts_even_with_lost_spaces() -> None:
    f = parse_ocr(
        [
            (
                2,
                [
                    line("Polo Ralph Lauren"),
                    line("SIZEM"),
                    line("67% COTTON"),
                    line("33% POLYESTER"),
                    line("RN 41381"),
                    line("MADEINPERU"),
                ],
            ),
        ]
    )
    assert f.size == "M" and f.size_photo == 2
    assert f.composition == {"cotton": 67, "polyester": 33}
    assert f.codes == {"rn": "41381"} and f.country == "Peru"
    assert f.brands == ["Ralph Lauren"] and f.brand_photo == 2
    assert f.label_photos == [2] and f.found


def test_italian_and_german_labels() -> None:
    it = parse_ocr([(0, [line("TAGLIA L"), line("80% COTONE"), line("20% POLIESTERE")])])
    assert it.size == "L" and it.composition == {"cotton": 80, "polyester": 20}
    de = parse_ocr([(1, [line("GRÖSSE XL"), line("100% BAUMWOLLE")])])
    assert de.size == "XL" and de.composition == {"cotton": 100}


def test_text_that_is_not_a_label_gives_no_facts() -> None:
    f = parse_ocr([(0, [line("SALE 50%"), line("Follow us"), line("HELLO")])])
    assert not f.found and f.label_photos == []


def test_a_barcode_is_a_code() -> None:
    assert parse_ocr([(0, [line("8056 1234 5678 9")])]).codes == {"barcode": "8056123456789"}


def test_only_one_size_is_kept_the_first() -> None:
    assert parse_ocr([(0, [line("SIZE M")]), (3, [line("SIZE XL")])]).size == "M"


# ------------------------------------------------------------------ from text to the label report and the dossier
def vision_with(facts_: Any) -> dict[str, Any]:
    from dataclasses import asdict

    return {
        "analyzer": "heuristic",
        "ocr_facts": asdict(facts_) | {"found": facts_.found},
        "photo_checks": [],
        "photo_hashes": [],
    }


def test_ocr_alone_makes_labels_readable_but_only_probable() -> None:
    v = vision_with(
        parse_ocr([(2, [line("SIZE M"), line("67% COTTON"), line("33% POLYESTER"), line("RN 41381")])])
    )
    rep = build_label_report(v, CLOTHING)
    by = {s.type: s for s in rep.states}
    assert by["size_label"].state == "present_readable" and by["size_label"].certainty == "probable"
    assert rep.size_text == "M" and rep.composition == {"cotton": 67, "polyester": 33}
    assert rep.codes["rn"] == "41381"
    # What OCR did not find is "insufficient information", not "not visible": nothing looked at the photos.
    assert by["care_label"].state in ("present_readable", "insufficient_information")
    assert by["paper_tag"].state == "insufficient_information"


def test_the_dossier_reads_labels_from_ocr_without_a_model_and_says_so() -> None:
    v = vision_with(parse_ocr([(2, [line("SIZE M"), line("100% COTTON")])]))
    d = build_dossier(facts(vision=v, declared_size="L"))
    p4 = next(p for p in d["passes"] if p["code"] == "P4")
    assert (
        p4["status"] == PARTIAL and "OCR locale" in p4["summary"] and "senza modello visivo" in p4["reason"]
    )
    p5 = next(p for p in d["passes"] if p["code"] == "P5")
    assert p5["status"] == "not_possible" and "l'OCR legge solo il testo" in p5["reason"]
    # Case F without any model: declared L, the label says M.
    c = next(c for c in d["contradictions"] if c["code"] == "size")
    assert "dichiarata L, etichetta M" in c["detail"]
    # The composition is checked against the declared material too.
    assert d["labels"]["composition"] == {"cotton": 100}


def test_without_ocr_and_without_a_model_the_label_pass_is_not_possible() -> None:
    p4 = next(p for p in build_dossier(facts(vision=None))["passes"] if p["code"] == "P4")
    assert p4["status"] == "not_possible" and "OCR" in p4["needs"]


# ------------------------------------------------------------------ the analyzer runs it on every decoded photo
class FakeEngine:
    name = "fake-ocr"

    def __init__(self) -> None:
        self.seen: list[tuple[int, int]] = []

    def read(self, image: Image.Image) -> list[OcrLine]:
        self.seen.append(image.size)
        return [line("SIZE M"), line("67% COTTON 33% POLYESTER")] if len(self.seen) == 2 else []


async def test_the_photo_analysis_reads_every_photo_and_keeps_the_text() -> None:
    from io import BytesIO

    def plain(color: str) -> bytes:
        buf = BytesIO()
        Image.new("RGB", (900, 1200), color).save(buf, "JPEG")
        return buf.getvalue()

    uploaded = [photo(i, plain(c)) for i, c in enumerate(["white", "grey", "black"])]
    engine = FakeEngine()
    set_ocr_engine(engine)
    out = await HeuristicImageAnalyzer().analyze(uploaded, {})
    assert len(engine.seen) == 3  # every photo, none skipped
    assert [p.photo for p in out.ocr] == [1] and out.ocr_engine == "fake-ocr"
    assert out.ocr_facts["size"] == "M" and out.ocr_facts["composition"] == {"cotton": 67, "polyester": 33}
    assert out.photo_quality.has_label_photo is True  # label-like text was read
    assert out.analyzer == "heuristic"  # still no model: OCR does not pretend to be one


async def test_without_an_engine_nothing_is_read_and_nothing_claimed() -> None:
    set_ocr_engine(None)
    out = await HeuristicImageAnalyzer().analyze([photo(0, jpeg(1))], {})
    assert out.ocr == [] and out.ocr_engine is None and out.photo_quality.has_label_photo is None


# ------------------------------------------------------------------ the real engine
def test_the_real_engine_reads_a_rendered_label(tmp_path: Path) -> None:
    pytest.importorskip("rapidocr_onnxruntime")
    img = Image.new("RGB", (760, 300), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=36)
    d.text((20, 20), "Polo Ralph Lauren  SIZE M", fill="black", font=font)
    d.text((20, 100), "67% COTTON  33% POLYESTER", fill="black", font=font)
    d.text((20, 180), "RN 41381   MADE IN PERU", fill="black", font=font)
    set_ocr_engine(None)
    ocr_mod._tried = False
    engine = ocr_mod.get_ocr_engine()
    assert engine is not None and engine.name == "rapidocr"
    facts_ = parse_ocr([(0, engine.read(img))])
    assert facts_.size == "M"
    assert facts_.composition == {"cotton": 67, "polyester": 33}
    assert facts_.codes.get("rn") == "41381" and facts_.country == "Peru"
    assert "Ralph Lauren" in facts_.brands
