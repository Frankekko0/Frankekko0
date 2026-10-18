"""What the identification engine understands from a title, a description and the marketplace's fields.

TEAM-004 ambient words are not teams, CAT-005 the category comes from the marketplace's own path, BRAND-006 a
wider catalogue and an unknown brand that stays unknown, SUSP-008 replica wording read with its context,
PROV-009 guesses are not facts, SIZE-011 sizes, MISC-012 colours, materials, codes, prices paid and bundles.
"""

import pytest

from app.analysis.text import analyze_text
from app.domain.enums import Certainty
from app.identification.engine import IdentificationEngine, ListingText
from app.ingestion.normalizer import (
    normalize_color,
    normalize_material,
    normalize_size,
    size_distance,
)
from tests.fixtures.extraction import batteries as B

ENGINE = IdentificationEngine()
JACKETS = "Uomo > Vestiti > Cappotti e giacche > Giacche"


def ident(title: str, desc: str = "", **kw: object):
    return ENGINE.identify(ListingText(title=title, description=desc, **kw))  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------- TEAM-004
@pytest.mark.parametrize(
    "desc",
    [
        "Made in Italy",
        "Spedizione in tutta Italia",
        "Ritiro a Roma",
        "Spedisco da Napoli",
        "Acquistato a Napoli nel 2021",
        "Inter-city train, 10 pezzi",
        "Felpa in jersey di cotone",
        "Prodotto in Italy",
        # a football word somewhere else in the text is not a football word next to the name
        "Made in Italy. La maglia è morbida e calda, tessuto di qualità",
        "Acquistata a Roma, away from home, third owner",
        "Maglia in pura lana, ritiro a Roma o spedizione tracciata",
        "Camicia estiva home made, ritiro a Napoli",
    ],
)
def test_ambient_words_are_not_a_team_a_model_or_a_football_shirt(desc: str) -> None:
    r = ident("Giacca nera", desc, brand_field="Zara", category_field=JACKETS)
    assert r.team.value is None and r.model.value is None
    assert r.category.value == "jackets"
    puffer = ident(
        "Piumino nero",
        desc,
        brand_field="Moncler",
        category_field="Uomo > Vestiti > Cappotti e giacche > Piumini",
    )
    assert (
        puffer.team.value is None and puffer.model.value is None and puffer.category.value == "puffer-jackets"
    )


@pytest.mark.parametrize(
    ("title", "team"),
    [
        ("Maglia Inter 2009/10 home", "Inter"),
        ("Maglia calcio Napoli Maradona", "Napoli"),
        ("Juventus maglia gara 2002/03", "Juventus"),
        ("Real Madrid home kit 2014", "Real Madrid"),
    ],
)
def test_a_team_is_read_next_to_a_football_word_or_a_season(title: str, team: str) -> None:
    r = ident(title)
    assert (r.team.value, r.category.value) == (team, "football-shirts")


def test_a_team_only_the_description_names_is_a_hint_not_the_model() -> None:
    r = ident("Maglia Nike originale taglia L", "Maglia ufficiale dell'Italia", brand_field="Nike")
    assert r.team.value == "Italia" and r.team.source == "description"
    assert r.model.value is None and r.category.value != "football-shirts"


def test_a_city_in_a_title_without_football_words_is_not_a_team() -> None:
    assert ident("Felpa Roma vintage").team.value is None
    assert ident("Souvenir Napoli").team.value is None


# --------------------------------------------------------------------------------------------- CAT-005
@pytest.mark.parametrize(
    ("crumb", "title", "slug"),
    [
        ("Uomo > Vestiti > Cappotti e giacche > Piumini", "Giubbino nero taglia M", "puffer-jackets"),
        ("Uomo > Vestiti > Maglioni e felpe > Felpe", "Nike Tech Fleece nera", "sweatshirts"),
        ("Uomo > Vestiti > Maglioni e felpe > Maglioni girocollo", "Maglione girocollo", "knitwear"),
        ("Uomo > Vestiti > Cappotti e giacche > Giacche di jeans", "Giacca di jeans", "jackets"),
        ("Uomo > Scarpe > Stivali", "Dr. Martens 1460 neri", "boots"),
        ("Uomo > Scarpe > Mocassini", "Gucci horsebit", "loafers"),
        ("Uomo > Scarpe > Sandali", "Birkenstock Arizona", "sandals"),
        ("Donna > Vestiti > Abiti > Abiti casual", "Zara a fiori", "dresses"),
        ("Donna > Vestiti > Gonne", "Gonna plissettata", "skirts"),
        ("Uomo > Vestiti > Pantaloncini", "Nike dri-fit", "shorts"),
        ("Uomo > Vestiti > Camicie e t-shirt > Polo > Polo Ralph Lauren Polo", "Polo t shirt", "polo-shirts"),
        ("Uomo > Vestiti > Jeans > Jeans straight fit", "Levi's 501", "jeans"),
        # the plural of a word the path prints in another language: "Jacken" is jackets, "Hosen" trousers
        ("Herren > Kleidung > Jacken & Mäntel > Jacken", "Schwarz Gr. M", "jackets"),
        ("Herren > Kleidung > Hosen", "Schwarz Gr. M", "trousers"),
    ],
)
def test_the_marketplace_path_decides_the_category(crumb: str, title: str, slug: str) -> None:
    r = ident(title, category_field=crumb)
    assert r.category.value == slug
    assert r.category.certainty == Certainty.CERTAIN and r.category.provenance == "declared"


def test_a_title_never_overrides_the_path_and_is_never_certain_alone() -> None:
    # A title that says "t-shirt" under the path "Polo": the path wins and the mismatch is reported.
    r = ident("Polo t shirt Ralph Lauren", category_field="Uomo > Vestiti > Camicie e t-shirt > Polo")
    assert r.category.value == "polo-shirts" and r.category.certainty == Certainty.CERTAIN
    # Words alone, however many, are only probable.
    words = ident("T-shirt Zara nera", "T-shirt in cotone, t-shirt basica, maglietta estiva")
    assert words.category.value == "t-shirts" and words.category.certainty == Certainty.PROBABLE


def test_group_entries_of_the_path_decide_nothing() -> None:
    r = ident("Maglione girocollo", category_field="Uomo > Vestiti > Maglioni e felpe")
    assert r.category.value == "knitwear" and r.category.certainty == Certainty.PROBABLE


def test_catalogue_ids_decide_the_category_in_any_language() -> None:
    r = ident(
        "Maglia blu", category_codes=("5-men", "2050-clothing", "76-tops-and-t-shirts", "5492-polo-shirts")
    )
    assert r.category.value == "polo-shirts" and r.category.certainty == Certainty.CERTAIN
    # A group slug (several kinds) names none, whatever the language of the words.
    assert ident("Zara", category_codes=("1-coats-and-jackets",)).category.certainty != Certainty.CERTAIN


def test_girocollo_and_generic_shoes_do_not_decide_alone() -> None:
    assert ident("Maglione girocollo lana").category.value == "knitwear"
    assert ident("Stivali neri").category.value == "boots"
    assert ident("Mocassini pelle").category.value == "loafers"
    assert ident("Scarpe uomo").category.value == "sneakers"  # a generic word, the weakest guess
    assert ident("Felpe e maglioni lotto").category.value in ("sweatshirts", "knitwear")


# --------------------------------------------------------------------------------------------- BRAND-006
def test_the_catalogue_knows_the_brands_of_the_italian_resale() -> None:
    reference = list(dict.fromkeys(B.BRAND_REFERENCE))
    known = [b for b in reference if ident(f"Giacca {b} nera", brand_field=b).brand.value]
    assert len(known) / len(reference) >= 0.9
    for name in B.COUNTERFEIT_HEAVY:
        slug = ident(f"Borsa {name}", brand_field=name).brand.value
        assert slug and float(ENGINE.tax.brand_by_slug[slug].counterfeit_risk) >= 0.3, name


@pytest.mark.parametrize(
    "name",
    [
        "Canada Goose",
        "Palm Angels",
        "Bape",
        "Miu Miu",
        "Yeezy",
        "Balmain",
        "Cartier",
        "Tiffany & Co",
        "Moose Knuckles",
    ],
)
def test_the_brands_copied_most_that_a_fresh_list_found_missing_are_known_with_a_prior(name: str) -> None:
    """A list of 194 names written after the catalogue (not the one it was built from) missed these: a Canada Goose
    jacket was an unknown brand at 0.2, outside the authenticity veto and the per-brand photo rules."""
    r = ident(f"Giacca {name} nera", brand_field=name)
    assert r.brand.value and float(ENGINE.tax.brand_by_slug[r.brand.value].counterfeit_risk) >= 0.3, name


def test_a_brand_outside_the_catalogue_stays_unknown_and_raises_the_doubt() -> None:
    from app.identification.priors import UNKNOWN_BRAND_RISK, counterfeit_prior

    r = ident("Giacca nera", brand_field="Marchio Sconosciuto Srl", category_field=JACKETS)
    assert r.brand.value is None and r.flags["unknown_brand"] == "Marchio Sconosciuto Srl"
    assert any("autenticità non valutabile" in e for e in r.evidence)
    assert UNKNOWN_BRAND_RISK > 0.1 and counterfeit_prior(None, r.as_dict()) == UNKNOWN_BRAND_RISK
    # A known brand keeps its own prior; "no brand" fields are absence, not an unknown brand.
    assert counterfeit_prior(0.05, r.as_dict()) == 0.05
    assert "unknown_brand" not in ident("Giacca", brand_field="Altro").flags


def test_a_suggested_luxury_brand_brings_its_prior_but_is_not_the_brand() -> None:
    from app.identification.priors import counterfeit_prior

    r = ident("Borsa Louis Vitton Neverfull")  # misspelt: a guess
    assert r.brand.value is None and r.brand_guess.value == "louis-vuitton"
    assert counterfeit_prior(None, r.as_dict()) >= 0.7


def test_ralph_lauren_sub_brands_are_not_the_polo_pool() -> None:
    assert ident("Giacca", brand_field="RRL").brand.value == "rrl"
    assert ident("Polo", brand_field="Polo Ralph Lauren").brand.value == "ralph-lauren"
    assert ident("Polo", brand_field="Lauren Ralph Lauren").brand.value == "lauren-ralph-lauren"


# --------------------------------------------------------------------------------------------- SUSP-008
@pytest.mark.parametrize("phrase", B.SUSP_POS)
def test_replica_wording_is_found_in_every_language(phrase: str) -> None:
    assert ident(phrase).suspicious_terms, phrase


@pytest.mark.parametrize("phrase", B.SUSP_BENIGN)
def test_wording_that_only_sounds_like_a_replica_is_not(phrase: str) -> None:
    assert ident("Felpa Nike", phrase).suspicious_terms == [], phrase


def test_an_authentic_item_with_a_receipt_is_not_vetoed_by_its_own_reassurance() -> None:
    r = ident(
        "Giubbotto Stone Island",
        "Originale, non è un fake, allego copia dello scontrino",
        brand_field="Stone Island",
    )
    assert r.suspicious_terms == [] and r.authenticity.value != "suspicious"


def test_tipo_x_never_sets_the_brand_of_the_item() -> None:
    for title in (
        "Felpa tipo Nike",
        "Giubbotto stile Stone Island",
        "Polo ispirata a Ralph Lauren",
        "Polo like Ralph Lauren",
    ):
        r = ident(title)
        assert r.brand.value is None and r.brand_guess.value is None and r.suspicious_terms, title
    # ...but a comparison next to the item's own brand is only a comparison.
    ok = ident(
        "Felpa Champion blu", "Simile a Nike come stile, ma è Champion originale", brand_field="Champion"
    )
    assert ok.brand.value == "champion" and ok.suspicious_terms == []


# --------------------------------------------------------------------------------------------- PROV-009
@pytest.mark.parametrize(
    ("title", "desc"),
    [
        ("Scarpe da corsa grigie", "NB: spedizione solo tracciata"),
        ("Sciarpa Himalayan cashmere", ""),
        ("Maschera da sci goggle", ""),
        ("Scarpe Edizione spezial", ""),
        ("T-shirt box logo", ""),
        ("Borsa in pelle napa", "pelle napa"),
        ("Pantaloni neri", "abbinabile a pantaloni Tommy o Levi"),
        ("Scarpe bianche", "Nike Air Force 1 nelle foto ma non incluse"),
    ],
)
def test_a_guess_is_never_the_brand_the_comparables_are_filtered_on(title: str, desc: str) -> None:
    r = ident(title, desc)
    assert r.brand.value is None, (title, desc)
    assert r.brand_name is None and r.product_key is None


def test_a_brand_the_description_states_is_the_brand_a_stray_mention_is_not() -> None:
    assert (
        ident("Giacca invernale nera", "Marca Moncler, acquistata a Milano nel 2021.").brand.value
        == "moncler"
    )
    assert ident("Maglione uomo", "Ralph Lauren originale, 100% lana").brand.value == "ralph-lauren"
    mention = ident("Pantaloni neri", "Da abbinare a una camicia Lacoste")
    assert mention.brand.value is None


def test_a_model_name_that_only_one_brand_uses_is_the_brand_a_shared_word_is_not() -> None:
    assert ident("Piumino Nuptse nero").brand.value == "the-north-face"
    assert ident("TNF Nuptse 700", brand_field=None).brand.value == "the-north-face"
    assert ident("Sciarpa Himalayan").brand.value is None


def test_the_field_and_the_title_naming_two_brands_is_a_contradiction_not_a_fact() -> None:
    r = ident("Felpa Adidas Originals blu M", brand_field="Nike")
    assert r.brand.value is None and r.brand_guess.value == "nike"
    assert r.flags["brand_conflict"] == {"field": "Nike", "title": "Adidas"}
    assert r.contradictions[0]["field"] == "brand" and r.contradictions[0]["severity"] == "high"
    # Nike and Air Jordan are one family: no disagreement.
    assert ident("Air Jordan 1 Low Nike", brand_field="Nike").brand.value == "nike"
    # A bundle may name several brands.
    assert ident("Bundle felpe Champion e Nike", brand_field="Champion").brand.value == "champion"


def test_every_attribute_says_where_it_comes_from() -> None:
    r = ident(
        "Piumino Nuptse nero",
        "Taglia M",
        brand_field=None,
        category_field="Uomo > Cappotti e giacche > Piumini",
        size_field="M",
    )
    assert r.category.provenance == "declared" and r.size.provenance == "declared"
    assert r.brand.provenance == "inferred" and r.brand.source == "model_keyword"
    assert r.as_dict()["brand"]["provenance"] == "inferred"
    assert ident("Felpa", brand_field="Nike").brand.provenance == "declared"


def test_a_bare_number_is_a_guess_at_the_scale_until_the_item_is_known() -> None:
    unknown = ident("Capo bello", size_field="42")
    assert unknown.size.certainty == Certainty.PROBABLE and unknown.size.confidence < 0.7
    known = ident("Nike Air Max", size_field="42", category_field="Uomo > Scarpe > Sneakers")
    assert known.size.value == "EU42" and known.size.certainty == Certainty.CERTAIN
    letters = ident("Felpa", size_field="M")
    assert letters.size.certainty == Certainty.CERTAIN


def test_a_size_field_against_a_size_in_the_title_is_a_contradiction_and_lowers_the_confidence() -> None:
    clean = ident(
        "Felpa Nike taglia M",
        brand_field="Nike",
        size_field="M",
        category_field="Uomo > Maglioni e felpe > Felpe",
    )
    clash = ident(
        "Felpa Nike taglia L",
        brand_field="Nike",
        size_field="M",
        category_field="Uomo > Maglioni e felpe > Felpe",
    )
    assert clean.contradictions == []
    assert [c["field"] for c in clash.contradictions] == ["size"] and clash.confidence < clean.confidence


@pytest.mark.parametrize(
    "title", ["Maglia Man Utd 2008 Rooney", "T-shirt Spider-Man Marvel", "Felpa Batman bambino"]
)
def test_man_inside_a_name_is_not_the_audience(title: str) -> None:
    assert ident(title).gender.value != "men"


def test_a_condition_in_the_title_against_the_condition_field_is_a_contradiction() -> None:
    clash = ident("Giubbotto Nuovo con cartellino", brand_field="Zara", condition="Buone condizioni")
    assert [(c["field"], c["declared"], c["found"]) for c in clash.contradictions] == [
        ("condition", "good", "new_with_tags")
    ]
    agree = ident("Giubbotto Nuovo con cartellino", brand_field="Zara", condition="Nuovo con cartellino")
    assert agree.contradictions == []
    # A field the label vocabulary does not know, or a title that says nothing, contradicts nothing.
    assert ident("Giubbotto Zara", condition="Buone condizioni").contradictions == []
    assert ident("Giubbotto Nuovo con cartellino", condition="Pessime").contradictions == []
    assert clash.confidence < agree.confidence


def test_the_gender_comes_from_the_marketplace_path() -> None:
    assert ident("Giacca", category_field="Donna > Vestiti > Giacche").gender.value == "women"
    assert ident("Giacca", category_field="Bambini > Vestiti > Giacche").gender.value == "kids"
    assert ident("Giacca", category_field="Uomo > Vestiti > Giacche").gender.provenance == "declared"


# --------------------------------------------------------------------------------------------- SIZE-011
@pytest.mark.parametrize(("raw", "category", "gender", "expected"), B.SIZES)
def test_sizes(raw: str, category: str | None, gender: str | None, expected: str | None) -> None:
    assert normalize_size(raw, category, gender) == expected


def test_a_shoe_size_is_the_same_whether_the_category_was_known_or_not() -> None:
    assert size_distance("IT42", "EU42") == 0 and size_distance("IT42", "EU43") == 1
    assert size_distance("XL", "XXL") == 1 and size_distance("XXXL", "XXXXL") == 1


def test_a_size_in_the_title_is_read_with_its_parenthesis_and_waist() -> None:
    assert ident("Pantaloni taglia 42 (S)").size.value == "S"
    assert ident("Dickies 874 W32 L32").size.value == "W32"


# --------------------------------------------------------------------------------------------- MISC-012
@pytest.mark.parametrize(("raw", "expected"), B.COLORS)
def test_colours_as_the_marketplace_prints_them(raw: str, expected: str) -> None:
    assert normalize_color(raw) == expected


@pytest.mark.parametrize(("title", "expected"), B.COLOR_TITLES)
def test_feminine_and_plural_colours_in_titles(title: str, expected: str) -> None:
    assert ident(title).color.value == expected


@pytest.mark.parametrize(("raw", "expected"), B.MATERIALS)
def test_materials_as_the_marketplace_prints_them(raw: str, expected: str) -> None:
    assert normalize_material(raw) == expected


@pytest.mark.parametrize(("text", "expected"), B.MATERIAL_TEXTS)
def test_the_main_material_of_a_text_is_the_biggest_share_and_never_an_imitation(
    text: str, expected: str
) -> None:
    assert normalize_material(text) == expected


@pytest.mark.parametrize(("desc", "expected"), B.PRODUCT_CODES)
def test_a_product_code_is_a_code_not_a_word(desc: str, expected: str | None) -> None:
    assert ident("Felpa", desc).product_code.value == expected


@pytest.mark.parametrize(("desc", "expected"), B.PAID)
def test_the_price_paid_is_read_only_next_to_a_currency(desc: str, expected: float | None) -> None:
    assert analyze_text("Giacca", desc).original_price == expected


@pytest.mark.parametrize(
    ("title", "desc", "pieces"),
    [
        ("Set 2 polo Ralph Lauren", "", 2),
        ("Completo giacca e pantaloni", "", 2),
        ("Lotto 3 felpe", "Lotto di 3 pezzi uomo taglia M", 3),
        ("Stock 10 pezzi", "", 10),
        ("Due polo Lacoste", "", 2),
        ("Giacca", "a lot of wear, ottime condizioni", None),
        ("Felpa", "Una felpa sola", None),
        ("Scarpe", "Completo di scatola originale", None),
    ],
)
def test_a_bundle_is_recognised(title: str, desc: str, pieces: int | None) -> None:
    assert analyze_text(title, desc).lot_pieces == pieces


def test_champions_is_not_a_misspelling_of_the_brand() -> None:
    r = ident("Campione di sci, felpa", "")
    assert "misspelled_brand" not in r.flags and r.brand_guess.value is None
