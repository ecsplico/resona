from resona_diarize import Turn, Word, assign_words, sections


def test_sections_number_speakers_by_first_word_so_a_wordless_turn_leaves_no_gap() -> None:
    # Diarizer speaker 1 only coughs before the first word; speakers 2 and 3 speak.
    turns = [Turn(0.0, 0.4, 1), Turn(0.5, 2.0, 2), Turn(2.0, 4.0, 3), Turn(4.0, 5.0, 2)]
    words = [
        Word(0.6, 1.0, " Guten"),
        Word(1.1, 1.8, " Morgen."),
        Word(2.2, 3.0, " Hallo."),
        Word(4.1, 4.8, " Danke."),
    ]

    groups = sections(words, assign_words(words, turns))

    assert [(g.speaker, g.text) for g in groups] == [(1, "Guten Morgen."), (2, "Hallo."), (1, "Danke.")]
