"""Phrase batteries for product extraction (IT / EN / FR / DE / ES), used by the regression tests and by
``tests/bench/measure_extraction.py`` (run it against any checkout to compare before and after).

Every table is hand-written ground truth. Nothing here is Vinted traffic: the phrases are realistic
seller wording, written for the purpose.
"""

from __future__ import annotations

# ----------------------------------------------------------------------------------------- defects
# (phrase, minimum severity the text implies): a seller states a flaw.
DEFECT_POS = [
    ("Piccola macchia sul colletto", "minor"),
    ("Ha un buco sulla manica", "severe"),
    ("Strappo vicino alla tasca", "severe"),
    ("Colore sbiadito dai lavaggi", "minor"),
    ("Presenta pilling", "minor"),
    ("Cerniera rotta", "severe"),
    ("Zip bloccata", "severe"),
    ("Cucitura aperta sul fianco", "moderate"),
    ("Orlo scucito", "moderate"),
    ("Filo tirato sulla manica", "minor"),
    ("Bottone mancante", "moderate"),
    ("Rammendo sul gomito", "moderate"),
    ("Cavallo rammendato", "moderate"),
    ("Foro sulla spalla", "severe"),
    ("Segni di usura sul fondo", "minor"),
    ("Suola consumata", "moderate"),
    ("Scollamento della suola", "severe"),
    ("Ingiallita sul colletto", "moderate"),
    ("Alone sotto le ascelle", "moderate"),
    ("Ha qualche difetto, vedi foto", "moderate"),
    ("Leggero difetto sul retro", "minor"),
    ("Macchiolina sul petto", "minor"),
    ("Da lavare, un po' sporco", "minor"),
    ("Odore di fumo", "moderate"),
    ("Graffi sulla fibbia", "minor"),
    ("Scuffed on the toe", "minor"),
    ("Small stain on the cuff", "minor"),
    ("Hole in the pocket", "severe"),
    ("Zipper broken", "severe"),
    ("Some fading", "minor"),
    ("Frayed hem", "moderate"),
    ("Missing button", "moderate"),
    ("Pilling on sleeves", "minor"),
    ("Tache sur la manche", "moderate"),
    ("Trou au coude", "severe"),
    ("Fermeture cassée", "severe"),
    ("Déchirure", "severe"),
    ("Mancha en la manga", "moderate"),
    ("Agujero en el bolsillo", "severe"),
    ("Fleck am Kragen", "moderate"),
    ("Loch im Ärmel", "severe"),
    ("Reißverschluss defekt", "severe"),
    ("Piccolo buco sulla manica e macchia sul colletto, leggermente scolorita, da lavare", "moderate"),
    ("Worn out soles", "moderate"),
    ("Vendo perché ha un difetto alla cerniera", "moderate"),
]
# A seller reassures: there is no defect (these must never count as declared defects).
DEFECT_NEG = [
    "Nessun difetto",
    "Senza difetti",
    "Non ha macchie né buchi",
    "Nessuna macchia, nessuno strappo",
    "Perfette condizioni, mai indossato",
    "Come nuovo, senza segni di usura",
    "No defects",
    "No stains or holes",
    "Sans défauts",
    "Sin defectos",
    "Keine Mängel",
    "Non presenta difetti",
    "Priva di difetti",
    "Non è rotto nulla",
    "Cerniera funzionante, nessun filo tirato",
    "Nessun segno di usura",
    "Condizioni perfette, nessun pilling",
    "Zip funziona perfettamente",
    "No rips, no tears, no fading",
    "Pas de trous ni de taches",
    # added in the continuation session: the denial after the word, and the possibility that is not an admission
    "Difetti: nessuno",
    "Difetti: assenti",
    "Macchie: no. Buchi: no.",
    "Stains: none. Holes: none.",
    "Défauts: aucun",
    "Buchi non ce ne sono",
    "Controllate le foto per eventuali difetti",
    "Se ci sono difetti li vedete nelle foto",
]

# ----------------------------------------------------------------------------------------- replicas
SUSP_POS = [
    "replica Nike",
    "Nike fake",
    "scarpe false",
    "imitazione Nike",
    "copia Nike",
    "contraffatto",
    "contraffazione",
    "counterfeit",
    "knock off Nike",
    "knockoff",
    "lookalike Nike",
    "look-alike",
    "Nike dupe",
    "1:1 Nike",
    "AAA quality",
    "mirror quality",
    "first copy",
    "super copy",
    "UA quality Nike",
    "copie Nike",
    "contrefaçon Nike",
    "imitation Nike",
    "Kopie Nike",
    "Fälschung Nike",
    "nachgemacht",
    "imitación Nike",
    "réplica Nike",
    "falsificación",
    "falso",
    "non originale",
    "qualità top replica",
    "stile Nike",
    "tipo Nike",
    "like Nike",
    "Nike style",
    "inspired by Nike",
    "ispirata a Nike",
    "ispirato Nike",
    "alta qualità replica",
    "Sac copie Chanel",
    "Giubbotto copia di alta qualità",
    "Cintura Gucci replica",
    "Nike Air Force 1 AAA quality",
    "senza marchio stile Stone Island",
    "Nike AF1 ua version",
    "Borsa Louis Vuitton 1:1",
    "Nike repliche perfette",
    "Tipo Ralph Lauren",
]
# Wording that sounds like a replica warning but is not one.
SUSP_BENIGN = [
    "Originale al 100%, non è un fake",
    "Garantito non replica",
    "No fake, no replica, acquistato in negozio",
    "Autentico, non copia",
    "condizioni simili al nuovo",
    "colore simile a quello della foto",
    "taglia simile alla L",
    "simile al nuovo",
    "Mai usata, simili a nuove",
    "Come nuova, simile a quella in negozio",
    "ispirato al design anni 90",
    "dall'aspetto vintage ispirato ai 70",
    "spedisco copia dello scontrino",
    "allego copia della fattura",
    "copia della ricevuta disponibile",
    "Copia scontrino inclusa",
    "invio copia dello scontrino",
    "Mirror selfie nelle foto",
    "Not a fake, original with receipt",
    "Authentique, pas une copie",
    "Original, keine Fälschung",
]

# ----------------------------------------------------------------------------------------- conditions
# What vinted.it (and the other domains) print in the condition field, and what it must mean.
# The first group is observed on real pages (tests/fixtures/vinted/real); the rest follows the printed
# vocabulary of the other languages the shared parser configuration lists.
CONDITION_LABELS = [
    ("Nuovo con cartellino", "new_with_tags"),
    ("Nuovo senza cartellino", "new_without_tags"),
    ("Ottime", "very_good"),
    ("Buone", "good"),
    ("Ottime condizioni", "very_good"),
    ("Buone condizioni", "good"),
    ("Soddisfacenti", "satisfactory"),
    ("Condizioni soddisfacenti", "satisfactory"),
    ("Discrete", "satisfactory"),
    ("Discrete condizioni", "satisfactory"),
    ("Ottimo", "very_good"),
    ("Buono", "good"),
    ("Neuf avec étiquette", "new_with_tags"),
    ("Neuf sans étiquette", "new_without_tags"),
    ("Très bon état", "very_good"),
    ("Bon état", "good"),
    ("Satisfaisant", "satisfactory"),
    ("Neu mit Etikett", "new_with_tags"),
    ("Neu ohne Etikett", "new_without_tags"),
    ("Sehr gut", "very_good"),
    ("Gut", "good"),
    ("Zufriedenstellend", "satisfactory"),
    ("Nuevo con etiquetas", "new_with_tags"),
    ("Nuevo sin etiquetas", "new_without_tags"),
    ("Muy bueno", "very_good"),
    ("Bueno", "good"),
    ("Satisfactorio", "satisfactory"),
    ("Nieuw met kaartje", "new_with_tags"),
    ("Nieuw zonder kaartje", "new_without_tags"),
    ("Heel goed", "very_good"),
    ("Goed", "good"),
    ("Redelijk", "satisfactory"),
    ("Very good", "very_good"),
    ("Good", "good"),
    ("Satisfactory", "satisfactory"),
    ("New with tags", "new_with_tags"),
    ("New without tags", "new_without_tags"),
    ("Nowy z metką", "new_with_tags"),
    ("Nowy bez metki", "new_without_tags"),
]
# Labels that must stay unknown (and be logged), never guessed.
CONDITION_UNKNOWN = ["Pessime", "Da collezione", "Stato: boh", "###", "Non specificato"]
# Free text: a condition is read only from an unambiguous phrase, never from a stray word.
CONDITION_TEXT = [
    ("Giacca usata ma in ottime condizioni, nessun difetto", "very_good"),
    ("Mai indossata, ancora con cartellino", "new_with_tags"),
    ("Buone condizioni, normali segni di usura", "good"),
    ("Good condition, worn a few times", "good"),
    ("Prezzo trattabile. Good brand, bella", "unknown"),
    ("Ottima qualità, vestibilità comoda", "unknown"),
    ("Spedizione buona e veloce", "unknown"),
    ("Das ist gut verpackt", "unknown"),
]

# ----------------------------------------------------------------------------------------- brands
# Brands that matter for the Italian resale of clothes, shoes and bags (a list written for the audit; it is
# not Vinted traffic data). ``COUNTERFEIT_HEAVY`` are the ones that need a real counterfeit prior.
BRAND_REFERENCE = [
    "Nike", "Adidas", "Ralph Lauren", "Polo Ralph Lauren", "Levi's", "Tommy Hilfiger", "Calvin Klein", "Lacoste",
    "The North Face", "Carhartt", "Carhartt WIP", "Stone Island", "Moncler", "C.P. Company", "Patagonia",
    "Arc'teryx", "Napapijri", "Woolrich", "Colmar", "Fila", "Ellesse", "Diadora", "Kappa", "Sergio Tacchini",
    "Champion", "Reebok", "Puma", "New Balance", "Converse", "Vans", "Jordan", "Timberland", "Dr. Martens",
    "Salomon", "Asics", "Columbia", "Helly Hansen", "Barbour", "Burberry", "Gucci", "Prada", "Louis Vuitton",
    "Armani", "Emporio Armani", "Giorgio Armani", "Armani Jeans", "Versace", "Dolce & Gabbana", "Balenciaga",
    "Off-White", "Fendi", "Dior", "Chanel", "Hermes", "Saint Laurent", "Valentino", "Hugo Boss", "Boss", "Diesel",
    "Guess", "Replay", "Pepe Jeans", "Wrangler", "Lee", "Superdry", "Hollister", "Abercrombie & Fitch", "Zara",
    "H&M", "Bershka", "Pull&Bear", "Stradivarius", "Mango", "Massimo Dutti", "Uniqlo", "Benetton", "Pinko",
    "Liu Jo", "Twinset", "Patrizia Pepe", "Max Mara", "Fay", "Herno", "Peuterey", "Aspesi", "Brunello Cucinelli",
    "Zegna", "Ermenegildo Zegna", "Fred Perry", "Dickies", "Supreme", "Stussy", "Umbro", "K-Way", "Gant",
    "Jack & Jones", "Michael Kors", "Ugg", "Birkenstock", "Crocs", "Hunter", "Alpha Industries", "Ben Sherman",
    "Superga", "Geox", "Tod's", "Hogan", "Philipp Plein", "Dsquared2", "Frankie Morello", "Maison Margiela",
    "Acne Studios", "Sandro", "Maje", "Ami Paris", "A.P.C.", "Lyle & Scott", "Pretty Green", "Mammut",
    "Jack Wolfskin", "Fjallraven", "Oakley", "Ray-Ban",
]  # fmt: skip
COUNTERFEIT_HEAVY = [
    "Gucci", "Prada", "Louis Vuitton", "Armani", "Versace", "Dolce & Gabbana", "Balenciaga", "Off-White", "Fendi",
    "Dior", "Chanel", "Hermes", "Saint Laurent", "Valentino", "Philipp Plein", "Dsquared2", "Michael Kors", "Jordan",
    "Ugg", "Burberry", "Stone Island", "Moncler", "Supreme",
]  # fmt: skip

# ----------------------------------------------------------------------------------------- sizes
# (raw size, category slug, gender, expected token); None expected = not derivable, must stay None.
SIZES = [
    ("IT 48 | W38", "jeans", None, "W38"),
    ("IT 52 | W36", "trousers", None, "W36"),
    ("IT 46 | S", "tops", None, "S"),
    ("XL | IT 52", None, None, "XL"),
    ("M / 38 / 10", None, None, "M"),
    ("L / 40 / 12", None, "women", "L"),
    ("42", "sneakers", None, "EU42"),
    ("EU 42 | UK 8 | US 9", "sneakers", None, "EU42"),
    ("UK 8", "sneakers", None, "EU42"),
    ("42.5", "sneakers", None, "EU42.5"),
    ("42,5", "sneakers", None, "EU42.5"),
    ("38", "sneakers", "women", "EU38"),
    ("38", "jeans", "women", "XS"),
    ("42", "jeans", "women", "M"),
    ("44", "trousers", "women", "L"),
    ("46", "trousers", None, "S"),
    ("48", "tops", None, "M"),
    ("42", "tops", "women", "M"),
    ("40", "tops", "women", "S"),
    ("50", "tops", None, "L"),
    ("S", "tops", None, "S"),
    ("Taglia unica", None, None, "ONESIZE"),
    ("One size", None, None, "ONESIZE"),
    ("TU", None, None, "ONESIZE"),
    ("W30 | L32", "jeans", None, "W30"),
    ("30", "jeans", None, "W30"),
    ("34", "jeans", None, "W34"),
    ("122 cm", "jackets", "kids", None),
    ("6-7 anni", "jackets", "kids", None),
    ("5 anni", None, "kids", None),
    ("XXXL", None, None, "XXXL"),
    ("3XL", None, None, "XXXL"),
    ("4XL", None, None, "XXXXL"),
    ("XS / S", None, None, "XS"),
    ("L-XL", None, None, "L"),
    ("M/L", None, None, "M"),
    ("44", "tops", None, "XS"),
    ("36", "sneakers", None, "EU36"),
    ("45", "sneakers", None, "EU45"),
    ("40", "sneakers", "kids", "EU40"),
    # a card has no category: an odd number is a shoe (no clothing scale has odd sizes), an even one stays a guess
    ("43", None, None, "EU43"),
    ("41", None, None, "EU41"),
    ("42", None, None, "IT42"),
]

# ----------------------------------------------------------------------------------------- colours / materials
# The multi-valued fields Vinted prints: the first one listed is the primary colour.
COLORS = [
    ("Blu marino", "navy"),
    ("Blu, Azzurro", "blue"),
    ("Bianco, Blu marino", "white"),
    ("Nero", "black"),
    ("Azzurro", "light_blue"),
    ("Verde", "green"),
    ("Verde militare", "khaki"),
    ("Turchese", "turquoise"),
    ("Argento", "silver"),
    ("Oro", "gold"),
    ("Corallo", "orange"),
    ("Fucsia", "pink"),
    ("Kaki", "khaki"),
    ("Bordeaux", "burgundy"),
    ("Multicolore", "multi"),
    ("Rosso", "red"),
    ("Giallo", "yellow"),
    ("Grigio, Nero", "grey"),
]
COLOR_TITLES = [
    ("Nike Air Max 270 nere", "black"),
    ("Scarpe bianche Adidas", "white"),
    ("Felpe rosse", "red"),
    ("Giacche grigie", "grey"),
    ("Maglie gialle", "yellow"),
    ("Jeans neri Zara", "black"),
]
MATERIALS = [
    ("Cotone, Denim", "cotton"),
    ("Poliestere", "polyester"),
    ("Pelle", "leather"),
    ("Pelle ecologica", "faux_leather"),
    ("Simil pelle", "faux_leather"),
    ("Cotone, Poliestere", "cotton"),
    ("Lana, Acrilico", "wool"),
    ("Acrilico", "acrylic"),
    ("Viscosa", "viscose"),
    ("Elastan", "elastane"),
    ("Cashmere", "cashmere"),
    ("Piuma", "down"),
    ("Seta", "silk"),
    ("Lino", "linen"),
]
# (free text, dominant material)
MATERIAL_TEXTS = [
    ("80% cotone 20% poliestere", "cotton"),
    ("30% lana 70% poliestere", "polyester"),
    ("100% acrilico effetto cashmere", "acrylic"),
    ("Ecopelle, interno foderato", "faux_leather"),
    ("Borsa in simil pelle", "faux_leather"),
    ("Camicia 100% lino", "linen"),
    ("Imbottitura 90% piuma d'oca", "down"),
    ("Giacca in pelle", "leather"),
]

# ----------------------------------------------------------------------------------------- product codes
# (description, expected code or None)
PRODUCT_CODES = [
    ("Articolo in ottime condizioni", None),
    ("Articolo originale al 100%", None),
    ("Style vintage anni 90", None),
    ("Referenza non disponibile", None),
    ("Codice articolo: CW2288-111", "CW2288-111"),
    ("Nike CW2288-111 bianche", "CW2288-111"),
    ("sku: DD1391-100", "DD1391-100"),
    ("Artigianale made in Italy", None),
    ("Reference photos available", None),
    ("Style casual", None),
    ("B75806 adidas", "B75806"),
    ("Cod. ABC-12345", "ABC-12345"),
]

# ----------------------------------------------------------------------------------------- paid price
PAID = [
    ("Pagata 250 euro nuova", 250.0),
    ("Acquistato a Roma 3 anni fa", None),
    ("Comprato a 120€ in negozio", 120.0),
    ("Retail 300 €", 300.0),
    ("Costava 180 euro", 180.0),
    ("Paid 90 euros", 90.0),
    ("Acquistata nel 2022 a 150 euro", 150.0),
    ("Marca Moncler, acquistata a Milano nel 2021. Taglia 2.", None),
]

# ----------------------------------------------------------------------------------------- brand consistency (matrix)
# Same brand, other spelling: never a contradiction.
BRAND_SAME = [
    ("Levi's", "Levi Strauss & Co."), ("Levi's", "LEVI'S"), ("Levi's", "LEVIS"), ("Arc'teryx", "ARCTERYX"),
    ("Arc'teryx", "Arc'teryx"), ("C.P. Company", "CP COMPANY"), ("C.P. Company", "C.P. Company"),
    ("The North Face", "NORTH FACE"), ("Tommy Hilfiger", "Tommy Jeans"), ("Tommy Hilfiger", "TOMMY HILFIGER"),
    ("H&M", "H & M"), ("Carhartt WIP", "CARHARTT"), ("Carhartt WIP", "Carhartt Work In Progress"),
    ("Ralph Lauren", "Polo Ralph Lauren"), ("Ralph Lauren", "POLO"), ("Nike", "NIKE"), ("Adidas", "adidas Originals"),
    ("Stüssy", "STUSSY"), ("Stone Island", "STONE ISLAND"), ("Lacoste", "LACOSTE"), ("Burberry", "Burberrys"),
    ("Napapijri", "NAPAPIJRI GEOGRAPHIC"),
]  # fmt: skip
# A real contradiction: another brand on the label.
BRAND_DIFFERENT = [
    ("Nike", "Adidas"),
    ("Stone Island", "Zara"),
    ("Moncler", "Peuterey"),
    ("Levi's", "Wrangler"),
]
# (title, description): a stray capital letter is not a size.
SIZE_TEXT_NOISE = [
    ("Felpa Nike M", "L'ho usata poche volte, ottime condizioni."),
    ("Felpa Nike S", "Vestibilità regolare. L'articolo è come nuovo."),
    ("Giacca Zara M", "Ho cambiato taglia, L'ho indossata 2 volte"),
    ("Polo Lacoste M", "Taglia M, la vendo perché troppo grande. S'intende nuova."),
]
# (title, description, label composition): garment words are not materials.
MATERIAL_NOISE = [
    ("Giubbotto piumino", "Piumino nero", {"polyamide": 100}),
    ("Jeans Levi's 501", "Jeans in denim blu", {"cotton": 100}),
    ("Camicia jeans", "Camicia in denim", {"cotton": 100}),
]
