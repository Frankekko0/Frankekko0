/* Generated from backend/app/acquisition/vinted_parser.json by tools/sync-parser-config.mjs.
 * Do not edit: change the JSON (the server and the extension share it) and run the tool. */
globalThis.FF_PARSER_CONFIG = {
  "version": "2026.10.6-1",
  "about": "Single source of Vinted selectors, labels and patterns, shared by the FlipFinder server (Python) and the browser extension (JavaScript). Regexes use only syntax common to both languages (numbered groups, no named groups). Edit here when Vinted changes its pages; the extension downloads it from the server.",
  "domains": [
    "vinted.it",
    "vinted.fr",
    "vinted.de",
    "vinted.es",
    "vinted.be",
    "vinted.nl",
    "vinted.lu",
    "vinted.pt",
    "vinted.at",
    "vinted.pl",
    "vinted.cz",
    "vinted.sk",
    "vinted.lt",
    "vinted.co.uk",
    "vinted.com",
    "vinted.se",
    "vinted.fi",
    "vinted.dk",
    "vinted.gr",
    "vinted.hr",
    "vinted.ro",
    "vinted.hu",
    "vinted.ie",
    "vinted.si",
    "vinted.lv",
    "vinted.ee"
  ],
  "patterns": {
    "item_id": {
      "source": "/items/(\\d+)",
      "flags": ""
    },
    "member_id": {
      "source": "/member/(\\d+)",
      "flags": ""
    },
    "page_item": {
      "source": "^/items/\\d+",
      "flags": ""
    },
    "page_favourites": {
      "source": "^/(member/items/favou?rite_list|favou?rites|favoris|favoriten|favoritos|ulubione)",
      "flags": "i"
    },
    "page_member": {
      "source": "^/member/\\d+",
      "flags": ""
    },
    "price_token": {
      "source": "(?:€|£|\\$|zł|kč|kr|ft|lei|eur)\\s?\\d{1,3}(?:[.,\\s\\u00a0\\u202f]\\d{3})*(?:[.,]\\d{1,2})?|\\d{1,3}(?:[.,\\s\\u00a0\\u202f]\\d{3})*(?:[.,]\\d{1,2})?\\s?(?:€|£|\\$|zł|kč|kr|ft|lei|eur)(?![a-z])",
      "flags": "i"
    },
    "protection_included": {
      "source": "(include|incl\\.?|includes|inclut|inkl\\.?|incluye|inclusief|zawiera|inclui|inkluderar)",
      "flags": "i"
    },
    "site_suffix": {
      "source": "\\s*[|\\-–]\\s*Vinted\\s*$",
      "flags": "i"
    },
    "status_sold": {
      "source": "\\b(venduto|sold|vendu|verkauft|vendido|verkocht|sprzedan[ey]|prodáno|predané|parduota|såld|solgt|myyty)\\b",
      "flags": "i"
    },
    "status_reserved": {
      "source": "\\b(riservato|reserved|réservé|reserviert|reservado|gereserveerd|zarezerwowan[ey]|rezervováno|rezervuota|reserverad|reserveret|varattu)\\b",
      "flags": "i"
    },
    "status_removed": {
      "source": "(non è più disponibile|è stato rimosso|no longer available|has been removed|n'est plus disponible|a été supprimé|nicht mehr verfügbar|wurde entfernt|ya no está disponible|niet meer beschikbaar|nie jest już dostępn)",
      "flags": "i"
    },
    "relative_now": {
      "source": "\\b(adesso|ora|just now|now|à l'instant|gerade eben|ahora|zojuist|teraz)\\b",
      "flags": "i"
    },
    "relative_yesterday": {
      "source": "\\b(ieri|yesterday|hier|gestern|ayer|gisteren|wczoraj)\\b",
      "flags": "i"
    },
    "relative_amount": {
      "source": "(\\d+)\\s*([a-zà-ÿ]+)",
      "flags": "i"
    },
    "embedded_favourites": {
      "source": "\\\\*\"favou?rite_count\\\\*\"\\s*:\\s*(\\d+)",
      "flags": ""
    },
    "embedded_views": {
      "source": "\\\\*\"view_count\\\\*\"\\s*:\\s*(\\d+)",
      "flags": ""
    },
    "embedded_reserved": {
      "source": "\\\\*\"is_reserved\\\\*\"\\s*:\\s*(true|false)",
      "flags": ""
    },
    "embedded_closed": {
      "source": "\\\\*\"is_closed\\\\*\"\\s*:\\s*(true|false)",
      "flags": ""
    },
    "embedded_closing_action": {
      "source": "\\\\*\"item_closing_action\\\\*\"\\s*:\\s*\\\\*\"(\\w+)\\\\*\"",
      "flags": ""
    },
    "embedded_created": {
      "source": "\\\\*\"created_at_ts\\\\*\"\\s*:\\s*\\\\*\"([0-9T:+\\-. Z]+)\\\\*\"",
      "flags": ""
    },
    "embedded_feedback_reputation": {
      "source": "\\\\*\"feedback_reputation\\\\*\"\\s*:\\s*([0-9.]+)",
      "flags": ""
    },
    "embedded_feedback_count": {
      "source": "\\\\*\"feedback_count\\\\*\"\\s*:\\s*(\\d+)",
      "flags": ""
    },
    "embedded_service_fee": {
      "source": "\\\\*\"service_fee\\\\*\"\\s*:\\s*\\{[^{}]*?\\\\*\"amount\\\\*\"\\s*:\\s*\\\\*\"?([0-9.]+)",
      "flags": ""
    },
    "embedded_material": {
      "source": "\\\\*\"material\\\\*\"\\s*:\\s*\\\\*\"([^\"\\\\]{2,60})\\\\*\"",
      "flags": ""
    },
    "email_sender": {
      "source": "vinted",
      "flags": "i"
    },
    "email_sold": {
      "source": "(è stato venduto|venduto|has been sold|was sold|a été vendu|wurde verkauft|se ha vendido|is verkocht|został sprzedany)",
      "flags": "i"
    },
    "email_price_drop": {
      "source": "(ribassat|prezzo .{0,20}(ridott|abbassat|sceso)|price .{0,20}(reduced|dropped|drop)|baiss|reduziert|rebajad|verlaagd|obniżon)",
      "flags": "i"
    },
    "email_new_items": {
      "source": "(nuov[oi] articol|new items?|nouvel(le)?s? articles?|neue artikel|nuevos? artículos?|nieuwe artikel)",
      "flags": "i"
    },
    "embedded_photo": {
      "source": "\\\\*\"full_size_url\\\\*\"\\s*:\\s*\\\\*\"(https?:(?:[^\"\\\\]|\\\\+/)+)",
      "flags": ""
    },
    "embedded_shipping": {
      "source": "\\\\*\"shipping_price\\\\*\"\\s*:\\s*\\{[^{}]*?\\\\*\"amount\\\\*\"\\s*:\\s*\\\\*\"?([0-9.]+)",
      "flags": ""
    }
  },
  "relative_units": {
    "minute": [
      "minut",
      "minute",
      "min",
      "mins",
      "minuto",
      "minuti"
    ],
    "hour": [
      "or",
      "ora",
      "ore",
      "hour",
      "hours",
      "hr",
      "heure",
      "heures",
      "stunde",
      "stunden",
      "hora",
      "horas",
      "uur",
      "godzin",
      "godzina",
      "godziny"
    ],
    "day": [
      "giorn",
      "giorno",
      "giorni",
      "day",
      "days",
      "jour",
      "jours",
      "tag",
      "tage",
      "tagen",
      "día",
      "días",
      "dag",
      "dagen",
      "dni",
      "dzień"
    ],
    "week": [
      "settiman",
      "settimana",
      "settimane",
      "week",
      "weeks",
      "semaine",
      "semaines",
      "woche",
      "wochen",
      "semana",
      "semanas",
      "weken",
      "tydzień",
      "tygodnie"
    ],
    "month": [
      "mes",
      "mese",
      "mesi",
      "month",
      "months",
      "mois",
      "monat",
      "monate",
      "meses",
      "maand",
      "maanden",
      "miesiąc",
      "miesiące"
    ],
    "year": [
      "ann",
      "anno",
      "anni",
      "year",
      "years",
      "an",
      "ans",
      "jahr",
      "jahre",
      "año",
      "años",
      "jaar",
      "jaren",
      "rok",
      "lata"
    ]
  },
  "labels": {
    "brand": [
      "brand",
      "marca",
      "marque",
      "marke",
      "merk",
      "marka",
      "značka",
      "prekės ženklas",
      "märke",
      "mærke",
      "μάρκα"
    ],
    "size": [
      "taglia",
      "size",
      "taille",
      "größe",
      "grösse",
      "talla",
      "maat",
      "rozmiar",
      "tamanho",
      "velikost",
      "dydis",
      "storlek",
      "størrelse",
      "koko",
      "μέγεθος"
    ],
    "condition": [
      "condizioni",
      "condition",
      "état",
      "etat",
      "zustand",
      "estado",
      "staat",
      "stan",
      "stav",
      "būklė",
      "skick",
      "stand",
      "kunto",
      "κατάσταση"
    ],
    "color": [
      "colore",
      "color",
      "colour",
      "couleur",
      "farbe",
      "kleur",
      "kolor",
      "cor",
      "barva",
      "spalva",
      "färg",
      "farve",
      "väri",
      "χρώμα"
    ],
    "material": [
      "materiale",
      "material",
      "matière",
      "matiere",
      "materiaal",
      "materiał",
      "materiál",
      "medžiaga",
      "materiaali"
    ],
    "uploaded": [
      "caricato",
      "uploaded",
      "ajouté",
      "hochgeladen",
      "subido",
      "geüpload",
      "dodane",
      "carregado",
      "nahráno",
      "įkelta",
      "uppladdad",
      "uploadet",
      "ladattu"
    ],
    "views": [
      "visualizzazioni",
      "views",
      "vues",
      "aufrufe",
      "visualizaciones",
      "weergaven",
      "wyświetlenia",
      "visualizações",
      "zhlédnutí",
      "peržiūros",
      "visningar"
    ],
    "favourites": [
      "interessati",
      "preferiti",
      "interested",
      "favourites",
      "favorites",
      "intéressés",
      "favoris",
      "interessenten",
      "favoriten",
      "interesados",
      "favoritos",
      "geïnteresseerd",
      "zainteresowani",
      "interessados"
    ],
    "location": [
      "località",
      "posizione",
      "location",
      "emplacement",
      "standort",
      "ubicación",
      "locatie",
      "lokalizacja",
      "localização"
    ],
    "category": [
      "categoria",
      "category",
      "catégorie",
      "kategorie",
      "categoría",
      "categorie",
      "kategoria"
    ]
  },
  "conditions": [
    [
      "new_with_tags",
      "(con cartellino|with tags|avec étiquettes?|avec etiquettes?|mit etikett|con etiquetas?|met labels?|z metk|com etiquetas?|med etiket)"
    ],
    [
      "new_without_tags",
      "(senza cartellino|without tags|sans étiquettes?|sans etiquettes?|ohne etikett|sin etiquetas?|zonder labels?|bez metek|sem etiquetas?|uten etikett|utan etikett)"
    ],
    [
      "very_good",
      "(ottim|very good|très bon|tres bon|sehr gut|muy bueno|zeer goed|bardzo dobr|muito bom|meget god|mycket bra)"
    ],
    [
      "good",
      "(buon|\\bgood\\b|bon état|bon etat|\\bgut\\b|\\bbueno\\b|\\bgoed\\b|\\bdobr|\\bbom\\b|\\bgod\\b|\\bbra\\b)"
    ],
    [
      "satisfactory",
      "(discret|satisfactory|satisfaisant|zufriedenstellend|satisfactorio|redelijk|zadowalaj|satisfatório|tilfredsstillende|okej)"
    ]
  ],
  "currencies": [
    [
      "EUR",
      "€|\\beur\\b"
    ],
    [
      "GBP",
      "£"
    ],
    [
      "PLN",
      "zł"
    ],
    [
      "CZK",
      "kč"
    ],
    [
      "HUF",
      "\\bft\\b"
    ],
    [
      "RON",
      "\\blei\\b"
    ],
    [
      "SEK",
      "\\bkr\\b"
    ],
    [
      "USD",
      "\\$"
    ]
  ],
  "selectors": {
    "item_link": "a[href*=\"/items/\"]",
    "jsonld": "script[type=\"application/ld+json\"]",
    "attribute_rows": "[data-testid*=\"item-attributes\"], [data-testid*=\"item-details\"], [itemprop=\"brand\"], [itemprop=\"color\"], [itemprop=\"size\"]",
    "description": "[itemprop=\"description\"], [data-testid*=\"item-description\"]",
    "breadcrumbs": "[data-testid*=\"breadcrumb\"] a, nav[aria-label*=\"readcrumb\"] a, [itemtype*=\"BreadcrumbList\"] [itemprop=\"name\"]",
    "gallery_images": "[data-testid*=\"item-photo\"] img, [data-testid*=\"photo-gallery\"] img, [class*=\"item-photo\"] img, [class*=\"item-photos\"] img",
    "status_badges": "[data-testid*=\"status\"], [data-testid*=\"closed\"], [data-testid*=\"reserved\"], [class*=\"item-status\"]",
    "seller_rating": "[data-testid*=\"rating\"] [aria-label], [data-testid*=\"rating\"], [class*=\"Rating\"] [aria-label]",
    "seller_reviews": "[data-testid*=\"rating\"] ~ *, [data-testid*=\"review\"], [data-testid*=\"feedback\"]",
    "seller_profile_link": "a[href*=\"/member/\"]",
    "favourite_count": "[data-testid*=\"favourite\"], [aria-label*=\"avorit\"], [aria-label*=\"referit\"], [aria-label*=\"avori\"]",
    "card_testid_suffix": "--([a-z-]+)$",
    "card_image": "img",
    "card_summary_link": "a[href*=\"/items/\"][title]"
  },
  "limits": {
    "title": 300,
    "description": 5000,
    "short": 120,
    "images": 20,
    "max_batch": 200
  }
};
