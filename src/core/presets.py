replacements = [
    (r"\s*Komma", ","),
    (r"\s*Punkt", "."),
    (r"\s*Doppel[\s.,]*punkt", ":"),
    (r"\s*Kapitel", "\n\n# "),
    (r"\s*Hashtag\s*", " #"),
    (r"\s*Absatz", "\n"),
    (r"\s*Anstrich", "\n- "),
    (r"[\s\.]*Eckige[\s.,]*Klammer[\s.,]*auf[\s.,]*", " ["),
    (r"[\s\.]*Eckige[\s.,]*Klammer[\s.,]*zu[\s.,]*", "] "),
    (r"[\s\.]*Klammer[\s.,]*auf[\s.,]*", " ("),
    (r"[\s\.]*Klammer[\s.,]*zu[\s.,]*", ") "),
    # (r"[\s\.]*Zitat[\s.,]*Anfang[\s.,]*", ' "'),
    # (r"[\s\.]*Zitat[\s.,]*Ende[\s.,]*", '" '),

    (r"#\s+Verlauf[\s.,]*", "# Verlauf\n"),
    (r"#\s+Medikation[\s.,]*", "# Medikation\n"),
    (r"#\s+Psychopathologischer\s*Befund[\s.,]*", "# Psychopathologischer Befund\n"),
    (r"#\s+Pro[cz]edere[\s.,]*", "# Procedere\n"),

    ("Monique", "Monic"),
    ("Serafine", "Seraphine"),
    ("Serafina", "Seraphine"),
    ("Lucy", "Lucie"),
    ("Nele", "Neele"),
    ("Mele", "Neele")
]

initial_prompts = [
    "wach",
    "bewusstseinsklar",
    "zu allen Qualitäten passend orientiert",
    "Im Kontakt zugewand",
    "berichtet geordent",
    "Denk- oder Wahrnehmungsstörungen",
    "Ich-Grenzen", 
    "manifeste Angst-oder Zwangssymptomatik",
    "Suizidalität"

]